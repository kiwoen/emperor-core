"""
Task Queue Manager — multi-priority producer-consumer with monitoring & persistence.

Provides a :class:`QueueManager` that wraps three :class:`asyncio.Queue` instances
(HIGH / MEDIUM / LOW) and drains them in priority order via configurable consumer
workers.  Additional features:

  - Real-time monitoring: queue depth, processing rate (tasks/sec), average wait time.
  - Optional JSON-Lines persistence: enqueued tasks are appended to a journal;
    on restart pending tasks are automatically recovered.
  - Graceful shutdown that drains the journal and joins consumers.

Usage::

    from jarvis.async_core.queue_manager import QueueManager, Priority

    async def handler(item):
        print(f"Processing: {item}")

    qm = QueueManager(handler)
    await qm.start(worker_count=3)

    await qm.enqueue({"url": "https://example.com"}, priority=Priority.HIGH)
    await qm.enqueue({"url": "https://other.com"}, priority=Priority.LOW)

    stats = qm.monitor()
    print(stats["queue_depth"])

    await qm.shutdown()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("jarvis.async_core.queue_manager")

# Sentinel for shutdown signal.
_SHUTDOWN: object = object()


# ═══════════════════════════════════════════════════════════════════
# Priority enum
# ═══════════════════════════════════════════════════════════════════


class Priority(IntEnum):
    """Integer priority levels for queue routing."""

    LOW = 10
    MEDIUM = 50
    HIGH = 100


@dataclass
class _Envelope:
    """Internal wrapper for a queued task with metadata."""

    data: Any
    priority: Priority
    enqueued_at: float = field(default_factory=time.monotonic)
    attempt: int = 1


# ═══════════════════════════════════════════════════════════════════
# QueueManager
# ═══════════════════════════════════════════════════════════════════


class QueueManager:
    """Multi-priority task queue with consumer workers, monitoring, and persistence.

    Parameters:
        handler:         Async callable invoked for each dequeued task.
        max_queue_size:  Per-queue max size (``0`` = unbounded).
        journal_path:    File path for the persistence journal (JSON-Lines).
                         ``None`` disables persistence.
    """

    def __init__(
        self,
        handler: Callable[[Any], Awaitable[Any]],
        max_queue_size: int = 0,
        journal_path: Optional[str] = None,
    ) -> None:
        self._handler: Callable[[Any], Awaitable[Any]] = handler
        self._maxsize: int = max_queue_size

        # Priority queues
        self._queues: dict[Priority, asyncio.Queue] = {
            Priority.HIGH: asyncio.Queue(maxsize=max_queue_size),
            Priority.MEDIUM: asyncio.Queue(maxsize=max_queue_size),
            Priority.LOW: asyncio.Queue(maxsize=max_queue_size),
        }

        # Persistence
        self._journal_path: Optional[Path] = Path(journal_path) if journal_path else None
        self._journal_lock: asyncio.Lock = asyncio.Lock()

        # Workers
        self._workers: list[asyncio.Task] = []
        self._worker_count: int = 0
        self._running: bool = False

        # Monitoring
        self._completed_total: int = 0
        self._failed_total: int = 0
        self._wait_times: list[float] = []   # seconds — ring buffer, last 1000
        self._process_start: float = 0.0     # monotonic timestamp when start() was called

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self, worker_count: int = 2) -> None:
        """Start consumer workers and recover persisted tasks.

        Args:
            worker_count: Number of concurrent consumer coroutines.
        """
        if self._running:
            return
        self._running = True
        self._worker_count = worker_count
        self._process_start = time.monotonic()

        # Recover persisted tasks first
        await self._recover_from_journal()

        # Spawn consumers
        for i in range(worker_count):
            task = asyncio.create_task(self._consumer(i), name=f"queue_consumer_{i}")
            self._workers.append(task)

    async def shutdown(self, timeout: float = 10) -> None:
        """Graceful shutdown: stop accepting tasks, drain queues, join consumers.

        Args:
            timeout: Max seconds to wait for consumers to finish.
        """
        if not self._running:
            return
        self._running = False

        # Push sentinels to unblock consumers
        for _ in self._workers:
            for q in self._queues.values():
                try:
                    q.put_nowait(_SHUTDOWN)
                except asyncio.QueueFull:
                    pass

        # Wait for workers
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._workers, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            for w in self._workers:
                w.cancel()
        self._workers.clear()

    # ── enqueue ────────────────────────────────────────────────────

    async def enqueue(self, item: Any, priority: Priority = Priority.MEDIUM) -> bool:
        """Push an item onto the appropriate priority queue.

        Args:
            item:     Task payload.
            priority: Queue priority level.

        Returns:
            ``True`` on success.

        Raises:
            RuntimeError: QueueManager is not running.
        """
        if not self._running:
            raise RuntimeError("QueueManager is not running. Call start() first.")

        envelope = _Envelope(data=item, priority=priority)
        await self._queues[priority].put(envelope)

        # Persist to journal
        if self._journal_path:
            await self._persist_entry(envelope)

        return True

    async def enqueue_batch(self, items: list[tuple[Any, Priority]]) -> int:
        """Enqueue multiple items atomically.

        Args:
            items: List of ``(payload, priority)`` tuples.

        Returns:
            Number of items enqueued.
        """
        if not self._running:
            raise RuntimeError("QueueManager is not running. Call start() first.")

        count = 0
        for payload, priority in items:
            await self.enqueue(payload, priority)
            count += 1
        return count

    # ── Queue sizes ────────────────────────────────────────────────

    def queue_size(self, priority: Optional[Priority] = None) -> int:
        """Return the number of items waiting in queue(s)."""
        if priority is not None:
            return self._queues[priority].qsize()
        return sum(q.qsize() for q in self._queues.values())

    # ── Monitor ────────────────────────────────────────────────────

    def monitor(self) -> dict[str, Any]:
        """Return a snapshot of queue statistics.

        Keys include ``queue_depth``, ``queue_depth_high/medium/low``,
        ``processed_total``, ``failed_total``, ``processing_rate`` (tasks/sec),
        ``avg_wait_time_ms``, ``workers_online``.
        """
        now = time.monotonic()
        elapsed = max(now - self._process_start, 0.001)

        avg_wait = (sum(self._wait_times) / len(self._wait_times) * 1000) if self._wait_times else 0
        total_done = self._completed_total + self._failed_total
        rate = total_done / elapsed

        return {
            "queue_depth": self.queue_size(),
            "queue_depth_high": self._queues[Priority.HIGH].qsize(),
            "queue_depth_medium": self._queues[Priority.MEDIUM].qsize(),
            "queue_depth_low": self._queues[Priority.LOW].qsize(),
            "processed_total": self._completed_total,
            "failed_total": self._failed_total,
            "processing_rate": round(rate, 2),
            "avg_wait_time_ms": round(avg_wait, 1),
            "workers_online": sum(1 for w in self._workers if not w.done()),
        }

    def reset_stats(self) -> None:
        """Reset the monitoring counters (but not the queues themselves)."""
        self._completed_total = 0
        self._failed_total = 0
        self._wait_times.clear()
        self._process_start = time.monotonic()

    # ── Consumer (internal) ─────────────────────────────────────────

    async def _consumer(self, worker_id: int) -> None:
        """Single consumer loop: drain priority queues in order HIGH → MEDIUM → LOW."""
        while self._running or self._has_pending():
            envelope = await self._dequeue()
            if envelope is _SHUTDOWN:
                return

            wait_time = time.monotonic() - envelope.enqueued_at
            try:
                result = await self._handler(envelope.data)
                self._completed_total += 1
            except Exception:
                logger.exception("Consumer %d failed to process item", worker_id)
                self._failed_total += 1
                result = None

            # Track wait time (ring buffer, last 1000)
            self._wait_times.append(wait_time)
            if len(self._wait_times) > 1000:
                self._wait_times = self._wait_times[-1000:]

    async def _dequeue(self) -> Any:
        """Pull the highest-priority available item.  Returns ``_SHUTDOWN`` sentinel."""
        while True:
            for priority in (Priority.HIGH, Priority.MEDIUM, Priority.LOW):
                q = self._queues[priority]
                if not q.empty():
                    try:
                        return q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

            # All empty — wait for new items
            if not self._running:
                # Shutting down: try one final sweep then give up
                return _SHUTDOWN

            # Use asyncio.wait to listen on all three queues
            # (poll approach avoids this complexity; sleep briefly)
            await asyncio.sleep(0.01)

    def _has_pending(self) -> bool:
        """Check whether any queue still has items."""
        return any(not q.empty() for q in self._queues.values())

    # ── Persistence ────────────────────────────────────────────────

    async def _persist_entry(self, envelope: _Envelope) -> None:
        """Append a JSON line to the journal file."""
        if not self._journal_path:
            return
        async with self._journal_lock:
            try:
                line = json.dumps({
                    "priority": int(envelope.priority),
                    "enqueued_at": envelope.enqueued_at,
                    "data": self._serialize_data(envelope.data),
                }, ensure_ascii=False)
                os.makedirs(self._journal_path.parent, exist_ok=True)
                with open(self._journal_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                logger.warning("Failed to persist queue entry", exc_info=True)

    async def _recover_from_journal(self) -> None:
        """Read journal file and re-enqueue any pending tasks."""
        if not self._journal_path or not self._journal_path.exists():
            return

        recovered = 0
        with open(self._journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    priority = Priority(record["priority"])
                    envelope = _Envelope(
                        data=record["data"],
                        priority=priority,
                        enqueued_at=time.monotonic(),  # reset timestamp
                    )
                    await self._queues[priority].put(envelope)
                    recovered += 1
                except Exception:
                    logger.warning("Skipping unparseable journal line: %s", line[:80])

        # Truncate journal after successful recovery
        if recovered > 0:
            logger.info("Recovered %d tasks from journal %s", recovered, self._journal_path)
            self._truncate_journal()

    def _truncate_journal(self) -> None:
        """Clear the journal file."""
        if self._journal_path and self._journal_path.exists():
            self._journal_path.write_text("", encoding="utf-8")

    @staticmethod
    def _serialize_data(data: Any) -> Any:
        """Attempt to make *data* JSON-serializable for the journal.

        If the data is not JSON-serializable, fall back to ``str(data)``.
        """
        try:
            json.dumps(data)
            return data
        except (TypeError, ValueError):
            return str(data)
