"""
Async Concurrent Executor — priority-aware task execution with concurrency control.

Provides a :class:`AsyncExecutor` that accepts callables (sync or async) and
executes them with:
  - Configurable concurrency limit (``asyncio.Semaphore``).
  - Priority queue (higher-priority tasks run first).
  - Per-task timeout control.
  - Task cancellation support.
  - ``submit()`` returning :class:`concurrent.futures.Future`-like handle.
  - ``map()`` for batch execution with result ordering.

Usage::

    from huanxin.async_core.executor import AsyncExecutor

    async with AsyncExecutor(max_concurrency=5, default_timeout=30) as ex:
        # single task
        fut = await ex.submit(my_coro, priority=10)
        result = await fut

        # batch
        results = await ex.map([coro_a, coro_b, coro_c])
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger("huanxin.async_core.executor")

T = TypeVar("T")

# Sentinel signal for stopping workers.
_SENTINEL: object = object()


# ═══════════════════════════════════════════════════════════════════
# Priority helpers
# ═══════════════════════════════════════════════════════════════════


class Priority(IntEnum):
    """Integer priority levels (higher value = higher priority)."""

    LOW = 10
    MEDIUM = 50
    HIGH = 100


@dataclass(order=True)
class _PrioritizedTask:
    """Comparable wrapper item for the priority heap (max-heap emulated via negated priority)."""

    priority: int  # negated so heapq (min-heap) gives highest actual priority first
    seq: int       # tie-breaker for stable ordering (FIFO within same priority)
    task: Any = field(compare=False)
    timeout: float = field(compare=False)


# ═══════════════════════════════════════════════════════════════════
# Task handle (Future-like)
# ═══════════════════════════════════════════════════════════════════


class TaskHandle:
    """A lightweight future-like handle returned by :meth:`AsyncExecutor.submit`.

    Supports ``await handle``, cancellation, and timeout.

    Attributes:
        done:       ``True`` after the task completes (success or failure).
        cancelled:  ``True`` if the task was cancelled via :meth:`cancel`.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        self._future: asyncio.Future = self._loop.create_future()
        self._cancel_event: asyncio.Event = asyncio.Event()

    # ── public state ───────────────────────────────────────────────

    @property
    def done(self) -> bool:
        return self._future.done()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ── await / result ─────────────────────────────────────────────

    def __await__(self):
        return self._future.__await__()

    def result(self, timeout: Optional[float] = None) -> Any:
        """Blocking helper — only call outside async context."""
        return self._future.result(timeout=timeout)

    def exception(self, timeout: Optional[float] = None) -> Optional[BaseException]:
        return self._future.exception(timeout=timeout)

    # ── lifecycle (called by executor) ─────────────────────────────

    def _set_result(self, value: Any) -> None:
        if not self._future.done():
            self._future.set_result(value)

    def _set_exception(self, exc: BaseException) -> None:
        if not self._future.done():
            self._future.set_exception(exc)

    # ── cancellation ───────────────────────────────────────────────

    def cancel(self) -> bool:
        """Request cancellation.  Returns ``True`` if the cancellation flag was set.

        The task will be skipped before execution or interrupted during a
        cooperative ``await self.handle._check_cancelled()`` inside the task body.
        """
        was_set = not self._cancel_event.is_set()
        self._cancel_event.set()
        return was_set

    async def _check_cancelled(self) -> None:
        """Cooperative cancellation point — the coroutine should ``await`` this periodically."""
        if self._cancel_event.is_set():
            raise asyncio.CancelledError("Task was cancelled")


# ═══════════════════════════════════════════════════════════════════
# AsyncExecutor
# ═══════════════════════════════════════════════════════════════════


class AsyncExecutor:
    """Priority-aware, concurrency-limited async task executor.

    Parameters:
        max_concurrency: Maximum number of concurrently executing tasks (Semaphore).
        default_timeout: Per-task timeout in seconds. ``None`` = no limit.
    """

    def __init__(
        self,
        max_concurrency: int = 10,
        default_timeout: Optional[float] = 60,
    ) -> None:
        self.max_concurrency: int = max_concurrency
        self.default_timeout: Optional[float] = default_timeout
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrency)

        # Priority queue (heap of _PrioritizedTask)
        self._heap: list[_PrioritizedTask] = []
        self._heap_lock: asyncio.Lock = asyncio.Lock()
        # Event-driven wakeup: ``submit``/``shutdown`` set this when they push a
        # task, so the dispatcher sleeps until work arrives instead of polling
        # the heap with ``asyncio.sleep(0.01)`` (which burned ~100 wakeups/s
        # while idle and added up to 10ms scheduling latency per submission).
        self._not_empty: asyncio.Event = asyncio.Event()
        self._seq_counter: itertools.count = itertools.count()

        # Internal state
        self._running: bool = False
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._stats: dict[str, Any] = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "total_duration_ms": 0.0,
        }

    # ── Context manager ────────────────────────────────────────────

    async def __aenter__(self) -> "AsyncExecutor":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.shutdown()

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the dispatcher coroutine."""
        if self._running:
            return
        self._running = True
        self._dispatcher_task = asyncio.create_task(self._dispatcher(), name="async_executor_dispatcher")

    async def shutdown(self, wait: bool = True) -> dict[str, Any]:
        """Graceful shutdown: stop dispatcher, optionally wait for inflight tasks.

        Returns final ``stats`` dict.
        """
        if not self._running:
            return self._stats
        self._running = False

        # Push sentinel to wake up dispatcher
        async with self._heap_lock:
            heapq.heappush(self._heap, _PrioritizedTask(priority=0, seq=next(self._seq_counter), task=_SENTINEL, timeout=0))
            # Wake the dispatcher so it observes the sentinel promptly.
            self._not_empty.set()

        if self._dispatcher_task:
            try:
                await asyncio.wait_for(self._dispatcher_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._dispatcher_task.cancel()

        return self._stats

    # ── submit ─────────────────────────────────────────────────────

    async def submit(
        self,
        coro_fn: Callable[..., Awaitable[T]],
        *args: Any,
        priority: int = Priority.MEDIUM,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> TaskHandle:
        """Submit a coroutine function for execution.

        Args:
            coro_fn:  An async callable (will be called with ``*args, **kwargs``).
            priority: Integer priority (higher = runs sooner).  See :class:`Priority`.
            timeout:  Per-task timeout override.  ``None`` = use executor default.

        Returns:
            :class:`TaskHandle` that can be ``await``-ed for the result.
        """
        handle = TaskHandle()
        timeout_val = timeout if timeout is not None else self.default_timeout

        async with self._heap_lock:
            heapq.heappush(
                self._heap,
                _PrioritizedTask(
                    priority=-priority,  # negate for max-heap behavior
                    seq=next(self._seq_counter),
                    task=(coro_fn, args, kwargs, handle, timeout_val),
                    timeout=timeout_val or 0,
                ),
            )
            self._stats["submitted"] += 1
            # Wake the dispatcher (replaces busy-polling on the heap).
            self._not_empty.set()

        return handle

    # ── map ────────────────────────────────────────────────────────

    async def map(
        self,
        coros: list[Callable[..., Awaitable[T]]],
        priority: int = Priority.MEDIUM,
        timeout: Optional[float] = None,
        cancel_on_error: bool = False,
    ) -> list[Any]:
        """Execute a batch of coroutines concurrently and return results in order.

        Args:
            coros:           List of async callables.
            priority:        Priority for all tasks in the batch.
            timeout:         Per-task timeout.
            cancel_on_error: If ``True``, cancel remaining tasks on first failure.

        Returns:
            List of results, with exceptions raised from the first failing task.
        """
        handles: list[TaskHandle] = []
        for c in coros:
            h = await self.submit(c, priority=priority, timeout=timeout)
            handles.append(h)

        results: list[Any] = []
        first_exc: Optional[BaseException] = None

        for i, h in enumerate(handles):
            try:
                results.append(await h)
            except BaseException as exc:  # noqa: BLE001 - surface both real errors and cancellations
                results.append(exc)
                # CancelledError is a BaseException (not Exception); a cancelled
                # handle here is almost always one we cancelled ourselves via
                # `cancel_on_error`, so it must NOT mask the first real failure.
                if cancel_on_error and first_exc is None and not isinstance(exc, asyncio.CancelledError):
                    first_exc = exc
                    # Cancel all remaining handles
                    for remaining in handles[i + 1 :]:
                        remaining.cancel()

        if first_exc is not None:
            raise first_exc

        return results

    # ── Stats ──────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        """Read-only snapshot of execution statistics."""
        return dict(self._stats)

    @property
    def queue_size(self) -> int:
        """Number of tasks waiting in the priority queue."""
        return len([t for t in self._heap if t.task is not _SENTINEL])

    # ── Dispatcher (internal) ──────────────────────────────────────

    async def _dispatcher(self) -> None:
        """Main loop: pop tasks from heap and schedule them with semaphore control."""
        inflight: set[asyncio.Task] = set()

        while self._running or inflight:
            # Pop next task (blocking when heap is empty but still running)
            item = await self._pop_next()
            if item is None:
                break  # shutdown signal

            coro_fn, args, kwargs, handle, timeout_val = item

            # Check cancellation before starting
            if handle.cancelled:
                handle._set_exception(asyncio.CancelledError("Task cancelled before execution"))
                self._stats["cancelled"] += 1
                continue

            # Acquire semaphore.
            # NOTE: _runner captures the loop variables by binding them as
            # arguments at create_task time. Defining it without parameters and
            # closing over `handle`/`coro_fn`/etc. would suffer from Python's
            # late-binding closure trap (all workers would operate on the LAST
            # iteration's values), causing every handle except the last to hang.
            async def _runner(h, cf, a, k, tv) -> None:
                async with self._semaphore:
                    if h.cancelled:
                        h._set_exception(asyncio.CancelledError("Task cancelled"))
                        self._stats["cancelled"] += 1
                        return

                    start = time.perf_counter()
                    try:
                        if tv is not None:
                            result = await asyncio.wait_for(
                                cf(*a, **k),
                                timeout=tv,
                            )
                        else:
                            result = await cf(*a, **k)
                        h._set_result(result)
                        self._stats["completed"] += 1
                    except asyncio.TimeoutError:
                        h._set_exception(TimeoutError(f"Task timed out after {tv}s"))
                        self._stats["failed"] += 1
                    except asyncio.CancelledError:
                        h._set_exception(asyncio.CancelledError("Task cancelled"))
                        self._stats["cancelled"] += 1
                    except Exception as exc:
                        h._set_exception(exc)
                        self._stats["failed"] += 1
                    finally:
                        elapsed = (time.perf_counter() - start) * 1000
                        self._stats["total_duration_ms"] += elapsed

            task = asyncio.create_task(
                _runner(handle, coro_fn, args, kwargs, timeout_val),
                name="exec_worker",
            )
            inflight.add(task)
            task.add_done_callback(inflight.discard)

            # Garbage-collect completed tasks occasionally
            if len(inflight) > self.max_concurrency * 4:
                inflight = {t for t in inflight if not t.done()}

        # Wait for remaining inflight tasks
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)

    async def _pop_next(self) -> Optional[tuple]:
        """Pop the next task tuple from the heap, or ``None`` for shutdown.

        Event-driven: instead of busy-polling the heap with ``asyncio.sleep``,
        the dispatcher sleeps on :attr:`_not_empty`, which ``submit`` and
        ``shutdown`` signal whenever they push. This removes idle wakeups and
        lets a task submitted to an empty queue start with zero polling delay.
        """
        while True:
            async with self._heap_lock:
                if self._heap:
                    item = heapq.heappop(self._heap)
                else:
                    item = None

            if item is None:
                if not self._running:
                    return None
                # Heap empty but still running — block until submit()/shutdown()
                # wakes us. ``set`` is level-triggered, so a push that raced in
                # between releasing the lock and awaiting here is not lost.
                await self._not_empty.wait()
                self._not_empty.clear()
                continue

            if item.task is _SENTINEL:
                return None  # shutdown signal

            return item.task
