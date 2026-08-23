"""
Hermes Event Log — append-only persistent message journal.

Stores all messages in a line-delimited JSON file for audit,
replay, and debugging. Designed for low overhead: writes are
async-buffered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from jarvis.hermes.bus import Message

logger = logging.getLogger("jarvis.hermes.event_log")


class EventLog:
    """Append-only JSON-lines event log.

    Each line is a JSON object representing one Message.
    Supports tailing (last N events) and replay.
    """

    def __init__(self, log_path: str, max_size_mb: int = 100) -> None:
        self.log_path = Path(log_path)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self._write_queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=10000)
        self._writer_task: Optional[asyncio.Task] = None
        self._line_count: int = 0

        # Ensure directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # Count existing lines
        if self.log_path.exists():
            with open(self.log_path, encoding="utf-8") as f:
                self._line_count = sum(1 for _ in f)
        logger.info("EventLog at %s (%d existing events)", self.log_path, self._line_count)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def append(self, message: Message) -> None:
        """Enqueue a message for async writing (non-blocking)."""
        try:
            self._write_queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("EventLog write queue full, dropping message %s", message.id)

    async def _writer_loop(self) -> None:
        """Background task: drain queue → write to file."""
        buffer: list[str] = []
        flush_interval = 0.5  # seconds

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                while True:
                    try:
                        msg = await asyncio.wait_for(self._write_queue.get(), timeout=flush_interval)
                        line = self._serialize(msg)
                        buffer.append(line)
                    except asyncio.TimeoutError:
                        pass  # flush timer

                    if buffer:
                        f.write("".join(buffer))
                        f.flush()
                        os.fsync(f.fileno())  # durability
                        self._line_count += len(buffer)
                        buffer.clear()

                    # Rotate if too large
                    if self.log_path.stat().st_size > self.max_size_bytes:
                        self._rotate()
        except asyncio.CancelledError:
            # Final flush on shutdown
            if buffer:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write("".join(buffer))
                    f.flush()
            logger.info("EventLog writer shut down gracefully")

    def _serialize(self, message: Message) -> str:
        """Serialize a message to a single JSON line."""
        record = {
            "id": message.id,
            "topic": message.topic.path,
            "type": message.type.value,
            "sender": message.sender,
            "timestamp": message.timestamp,
            "correlation_id": message.correlation_id,
            "payload": self._sanitize_payload(message.payload),
        }
        return json.dumps(record, ensure_ascii=False) + "\n"

    @staticmethod
    def _sanitize_payload(payload: Any) -> Any:
        """Make payload JSON-serializable."""
        if isinstance(payload, (str, int, float, bool, type(None))):
            return payload
        if isinstance(payload, (list, tuple)):
            return [EventLog._sanitize_payload(x) for x in payload]
        if isinstance(payload, dict):
            return {str(k): EventLog._sanitize_payload(v) for k, v in payload.items()}
        return str(payload)[:500]

    def _rotate(self) -> None:
        """Rename current log and start fresh."""
        import time
        backup = self.log_path.with_suffix(f".{int(time.time())}.log")
        self.log_path.rename(backup)
        logger.info("EventLog rotated → %s", backup.name)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the last N events as dicts (for debugging)."""
        if not self.log_path.exists():
            return []
        lines = []
        with open(self.log_path, encoding="utf-8") as f:
            # Simple tail: read all, keep last N
            all_lines = f.readlines()
            lines = all_lines[-n:]
        return [json.loads(line) for line in lines if line.strip()]

    def replay(self, topic_filter: Optional[str] = None) -> list[dict[str, Any]]:
        """Replay all events, optionally filtered by topic pattern."""
        if not self.log_path.exists():
            return []
        import fnmatch
        events = []
        with open(self.log_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if topic_filter is None or fnmatch.fnmatch(record.get("topic", ""), topic_filter):
                        events.append(record)
                except json.JSONDecodeError:
                    continue
        return events

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._writer_task = asyncio.create_task(self._writer_loop())
        logger.info("EventLog writer started")

    async def shutdown(self) -> None:
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
        logger.info("EventLog shut down (%d total events)", self._line_count)

    @property
    def line_count(self) -> int:
        return self._line_count
