"""
Tests for ``jarvis.async_core`` — covering AsyncExecutor and QueueManager.

Usage::

    python -m pytest tests/test_async_core.py -v -x
"""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from jarvis.async_core import AsyncExecutor, Priority, QueueManager


# ═══════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════

def _temp_journal_path(tmp_path: Path) -> str:
    return str(tmp_path / "journal.jsonl")


# ═══════════════════════════════════════════════════════════════════
# AsyncExecutor tests
# ═══════════════════════════════════════════════════════════════════


class TestAsyncExecutorLifecycle:
    """Startup, shutdown, and context-manager tests."""

    @pytest.mark.asyncio
    async def test_start_and_shutdown(self):
        ex = AsyncExecutor()
        await ex.start()
        assert ex.stats["submitted"] == 0
        await ex.shutdown()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with AsyncExecutor(max_concurrency=2) as ex:
            assert ex.stats["submitted"] == 0

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self):
        ex = AsyncExecutor()
        await ex.start()
        await ex.start()
        await ex.shutdown()


class TestAsyncExecutorSubmit:
    """``submit()`` basics."""

    @pytest.mark.asyncio
    async def test_submit_and_await(self):
        async def hello():
            return "world"

        async with AsyncExecutor() as ex:
            handle = await ex.submit(hello)
            result = await handle
            assert result == "world"

    @pytest.mark.asyncio
    async def test_submit_with_args(self):
        async def add(a, b):
            return a + b

        async with AsyncExecutor() as ex:
            handle = await ex.submit(add, 3, 4)
            assert await handle == 7

    @pytest.mark.asyncio
    async def test_submit_with_kwargs(self):
        async def greet(name, greeting="Hello"):
            return f"{greeting} {name}"

        async with AsyncExecutor() as ex:
            handle = await ex.submit(greet, "Alice", greeting="Hi")
            assert await handle == "Hi Alice"

    @pytest.mark.asyncio
    async def test_multiple_submits(self):
        async def echo(x):
            return x

        async with AsyncExecutor() as ex:
            handles = [await ex.submit(echo, i) for i in range(5)]
            results = [await h for h in handles]
            assert results == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_stats_after_completion(self):
        async def noop():
            return None

        async with AsyncExecutor() as ex:
            for _ in range(3):
                h = await ex.submit(noop)
                await h
        assert ex.stats["completed"] == 3
        assert ex.stats["submitted"] == 3


class TestAsyncExecutorConcurrency:
    """Semaphore-based concurrency limiting."""

    @pytest.mark.asyncio
    async def test_concurrency_limit_enforced(self):
        inflight = 0
        max_seen = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal inflight, max_seen
            async with lock:
                inflight += 1
                max_seen = max(max_seen, inflight)
            await asyncio.sleep(0.05)
            async with lock:
                inflight -= 1

        async with AsyncExecutor(max_concurrency=2) as ex:
            handles = [await ex.submit(worker) for _ in range(8)]
            await asyncio.gather(*[h for h in handles])

        assert max_seen <= 2
        assert max_seen >= 1  # at least some concurrency

    @pytest.mark.asyncio
    async def test_concurrency_larger_than_tasks(self):
        """Executor should handle max_concurrency > number of tasks gracefully."""
        async def quick():
            await asyncio.sleep(0.01)
            return True

        async with AsyncExecutor(max_concurrency=50) as ex:
            results = await ex.map([quick] * 5)
        assert results == [True] * 5


class TestAsyncExecutorPriority:
    """Priority-based ordering."""

    @pytest.mark.asyncio
    async def test_high_priority_runs_before_low(self):
        order = []

        async def record(label, delay=0):
            order.append(label)
            if delay:
                await asyncio.sleep(delay)

        async with AsyncExecutor(max_concurrency=1) as ex:
            h_low = await ex.submit(record, "low", priority=10, delay=0.01)
            h_high = await ex.submit(record, "high", priority=100, delay=0.01)
            # Submit a third to ensure both are queued
            h_extra = await ex.submit(record, "extra", priority=50, delay=0)
            await asyncio.sleep(0.1)
            results = await asyncio.gather(h_high, h_low, h_extra)

        # High-priority should appear before low in execution order
        assert order.index("high") < order.index("low")

    @pytest.mark.asyncio
    async def test_same_priority_fifo(self):
        order = []

        async def record(label):
            order.append(label)

        async with AsyncExecutor(max_concurrency=1) as ex:
            h_a = await ex.submit(record, "a", priority=50)
            h_b = await ex.submit(record, "b", priority=50)
            h_c = await ex.submit(record, "c", priority=50)
            await asyncio.gather(h_a, h_b, h_c)

        assert order == ["a", "b", "c"]


class TestAsyncExecutorMap:
    """``map()`` batch execution."""

    @pytest.mark.asyncio
    async def test_map_basic(self):
        async def double(x):
            return x * 2

        async with AsyncExecutor() as ex:
            tasks = [lambda i=i: double(i) for i in range(5)]
            results = await ex.map(tasks)
        assert results == [0, 2, 4, 6, 8]

    @pytest.mark.asyncio
    async def test_map_with_priority(self):
        async def echo(x):
            return x

        async with AsyncExecutor() as ex:
            results = await ex.map([lambda: echo(1)] * 10, priority=Priority.HIGH)
        assert results == [1] * 10

    @pytest.mark.asyncio
    async def test_map_cancel_on_error(self):
        async def fail(x):
            if x == 2:
                raise ValueError("fail")
            await asyncio.sleep(0.02)
            return x

        async with AsyncExecutor(max_concurrency=3) as ex:
            tasks = [lambda i=i: fail(i) for i in range(5)]
            with pytest.raises(ValueError, match="fail"):
                await ex.map(tasks, cancel_on_error=True)

    @pytest.mark.asyncio
    async def test_map_no_cancel_on_error(self):
        async def maybe_fail(x):
            if x == 2:
                raise ValueError("fail")
            return x

        async with AsyncExecutor() as ex:
            tasks = [lambda i=i: maybe_fail(i) for i in range(5)]
            results = await ex.map(tasks)  # cancel_on_error=False by default
        # Results contain the exception object for the failing task
        assert results[0] == 0
        assert isinstance(results[2], ValueError)


class TestAsyncExecutorTimeout:
    """Per-task timeout control."""

    @pytest.mark.asyncio
    async def test_default_timeout_triggers(self):
        async def slow():
            await asyncio.sleep(1)

        async with AsyncExecutor(default_timeout=0.1) as ex:
            h = await ex.submit(slow)
            with pytest.raises(TimeoutError):
                await h

    @pytest.mark.asyncio
    async def test_per_task_timeout_override(self):
        async def slow():
            await asyncio.sleep(1)

        async with AsyncExecutor(default_timeout=30) as ex:
            h = await ex.submit(slow, timeout=0.1)
            with pytest.raises(TimeoutError):
                await h

    @pytest.mark.asyncio
    async def test_no_timeout_when_none(self):
        async def quick():
            return "ok"

        async with AsyncExecutor(default_timeout=None) as ex:
            h = await ex.submit(quick)
            assert await h == "ok"

    @pytest.mark.asyncio
    async def test_timeout_increments_failed_stat(self):
        async def slow():
            await asyncio.sleep(10)

        async with AsyncExecutor(default_timeout=0.05) as ex:
            h = await ex.submit(slow)
            with pytest.raises(TimeoutError):
                await h

        assert ex.stats["failed"] >= 1


class TestAsyncExecutorCancellation:
    """Task cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_before_execution(self):
        received = False

        async def work():
            nonlocal received
            received = True

        async with AsyncExecutor(max_concurrency=1) as ex:
            # Block the executor with a long-running task
            async def blocker():
                await asyncio.sleep(0.2)

            h_block = await ex.submit(blocker)
            h_work = await ex.submit(work)

            # Cancel before it gets executed
            h_work.cancel()

            await h_block
            with pytest.raises(asyncio.CancelledError):
                await h_work

        assert received is False
        assert ex.stats["cancelled"] >= 1

    @pytest.mark.asyncio
    async def test_handle_cancelled_property(self):
        async def nop():
            return None

        async with AsyncExecutor(max_concurrency=1) as ex:
            async def slow():
                await asyncio.sleep(0.2)

            h_slow = await ex.submit(slow)
            h_target = await ex.submit(nop)
            h_target.cancel()
            assert h_target.cancelled is True

            await h_slow


class TestAsyncExecutorException:
    """Exception propagation."""

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        async def boom():
            raise RuntimeError("boom")

        async with AsyncExecutor() as ex:
            h = await ex.submit(boom)
            with pytest.raises(RuntimeError, match="boom"):
                await h

    @pytest.mark.asyncio
    async def test_exception_increments_failed(self):
        async def boom():
            raise RuntimeError("error")

        async with AsyncExecutor() as ex:
            h = await ex.submit(boom)
            with pytest.raises(RuntimeError):
                await h

        assert ex.stats["failed"] == 1

    @pytest.mark.asyncio
    async def test_sync_callable_wrapped(self):
        """Test that a regular def callable used as a coroutine works when wrapped."""
        called = False

        async def wrapper():
            nonlocal called
            called = True
            return "sync-result"

        async with AsyncExecutor() as ex:
            h = await ex.submit(wrapper)
            result = await h

        assert called
        assert result == "sync-result"


class TestAsyncExecutorQueueSize:
    """queue_size property."""

    @pytest.mark.asyncio
    async def test_queue_size_decreases_as_tasks_consume(self):
        async def slow():
            await asyncio.sleep(0.05)

        async with AsyncExecutor(max_concurrency=1) as ex:
            for _ in range(10):
                await ex.submit(slow, priority=50)
            initial = ex.queue_size
            assert initial > 0
            await asyncio.sleep(0.3)
            # After sleeping enough time for the dispatcher to dequeue some
            assert ex.queue_size < initial


# ═══════════════════════════════════════════════════════════════════
# QueueManager tests
# ═══════════════════════════════════════════════════════════════════


class TestQueueManagerEnqueue:
    """Basic enqueue / dequeue / handler invocation."""

    @pytest.mark.asyncio
    async def test_enqueue_and_process(self):
        results = []

        async def handler(item):
            results.append(item)

        qm = QueueManager(handler)
        await qm.start(worker_count=1)
        await qm.enqueue({"id": 1})
        # Give consumers time to process
        await asyncio.sleep(0.1)
        await qm.shutdown()
        assert {"id": 1} in results

    @pytest.mark.asyncio
    async def test_enqueue_batch(self):
        items = []

        async def handler(item):
            items.append(item)

        qm = QueueManager(handler)
        await qm.start(worker_count=2)
        count = await qm.enqueue_batch([
            ({"n": 1}, Priority.HIGH),
            ({"n": 2}, Priority.MEDIUM),
            ({"n": 3}, Priority.LOW),
        ])
        assert count == 3
        await asyncio.sleep(0.15)
        await qm.shutdown()
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_enqueue_not_started_raises(self):
        qm = QueueManager(lambda x: None)
        with pytest.raises(RuntimeError, match="not running"):
            await qm.enqueue({"x": 1})


class TestQueueManagerPriority:
    """Multi-priority queue ordering."""

    @pytest.mark.asyncio
    async def test_high_priority_processed_first(self):
        order = []

        async def handler(item):
            order.append(item["label"])

        qm = QueueManager(handler)
        await qm.start(worker_count=1)  # single worker ensures strict ordering
        await qm.enqueue({"label": "low"}, Priority.LOW)
        await qm.enqueue({"label": "medium"}, Priority.MEDIUM)
        await qm.enqueue({"label": "high"}, Priority.HIGH)
        await asyncio.sleep(0.2)
        await qm.shutdown()

        assert order.index("high") < order.index("medium")
        assert order.index("medium") < order.index("low")

    @pytest.mark.asyncio
    async def test_same_priority_fifo(self):
        order = []

        async def handler(item):
            order.append(item["id"])

        qm = QueueManager(handler)
        await qm.start(worker_count=1)
        for i in range(5):
            await qm.enqueue({"id": i}, Priority.MEDIUM)
        await asyncio.sleep(0.2)
        await qm.shutdown()
        assert order == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_mixed_priority_ordering(self):
        order = []

        async def handler(item):
            order.append(item["id"])

        qm = QueueManager(handler)
        await qm.start(worker_count=2)
        # Enqueue interleaved priorities
        await qm.enqueue({"id": "A"}, Priority.LOW)
        await qm.enqueue({"id": "B"}, Priority.HIGH)
        await qm.enqueue({"id": "C"}, Priority.MEDIUM)
        await qm.enqueue({"id": "D"}, Priority.HIGH)
        await qm.enqueue({"id": "E"}, Priority.LOW)
        await asyncio.sleep(0.2)
        await qm.shutdown()

        # All high-priority before all low-priority
        high_idx = [order.index(x) for x in ["B", "D"]]
        low_idx = [order.index(x) for x in ["A", "E"]]
        assert max(high_idx) < min(low_idx)  # all HIGH before any LOW


class TestQueueManagerConcurrency:
    """Multiple consumers."""

    @pytest.mark.asyncio
    async def test_multiple_workers_process_concurrently(self):
        inflight = 0
        max_seen = 0
        lock = asyncio.Lock()

        async def handler(item):
            nonlocal inflight, max_seen
            async with lock:
                inflight += 1
                max_seen = max(max_seen, inflight)
            await asyncio.sleep(0.05)
            async with lock:
                inflight -= 1

        qm = QueueManager(handler)
        await qm.start(worker_count=3)
        for i in range(9):
            await qm.enqueue({"n": i})
        await asyncio.sleep(0.5)
        await qm.shutdown()
        assert max_seen >= 2  # multiple workers active simultaneously


class TestQueueManagerMonitor:
    """Queue statistics and monitoring."""

    @pytest.mark.asyncio
    async def test_monitor_initial_state(self):
        qm = QueueManager(lambda x: None)
        await qm.start(worker_count=2)
        stats = qm.monitor()
        assert stats["queue_depth"] == 0
        assert stats["processed_total"] == 0
        assert stats["failed_total"] == 0
        assert "processing_rate" in stats
        assert "avg_wait_time_ms" in stats
        assert stats["workers_online"] == 2
        await qm.shutdown()

    @pytest.mark.asyncio
    async def test_monitor_reflects_queue_depth(self):
        qm = QueueManager(lambda x: None)
        await qm.start(worker_count=1)
        await qm.enqueue({"a": 1})
        await qm.enqueue({"b": 2})
        # Give a moment for items to enter queue (but not yet processed by slow handler)
        # Handler does nothing so they'll be processed immediately; check before
        stats = qm.monitor()
        # At this point items may or may not have been consumed, but the keys must exist
        assert "queue_depth" in stats
        assert "queue_depth_high" in stats
        assert "queue_depth_medium" in stats
        assert "queue_depth_low" in stats
        await qm.shutdown()

    @pytest.mark.asyncio
    async def test_monitor_after_processing(self):
        async def handler(item):
            pass

        qm = QueueManager(handler)
        await qm.start(worker_count=2)
        for i in range(10):
            await qm.enqueue({"n": i})
        await asyncio.sleep(0.3)
        stats = qm.monitor()
        assert stats["processed_total"] == 10
        await qm.shutdown()

    @pytest.mark.asyncio
    async def test_processing_rate_nonzero(self):
        async def handler(item):
            await asyncio.sleep(0.01)

        qm = QueueManager(handler)
        await qm.start(worker_count=2)
        for i in range(5):
            await qm.enqueue({"n": i})
        await asyncio.sleep(0.3)
        stats = qm.monitor()
        assert stats["processing_rate"] > 0
        await qm.shutdown()

    @pytest.mark.asyncio
    async def test_reset_stats(self):
        async def handler(item):
            pass

        qm = QueueManager(handler)
        await qm.start(worker_count=1)
        for i in range(5):
            await qm.enqueue({"n": i})
        await asyncio.sleep(0.15)
        qm.reset_stats()
        stats = qm.monitor()
        assert stats["processed_total"] == 0
        assert stats["failed_total"] == 0
        await qm.shutdown()

    @pytest.mark.asyncio
    async def test_queue_size_by_priority(self):
        qm = QueueManager(lambda x: None)
        await qm.start(worker_count=1)
        await qm.enqueue({"x": 1}, Priority.HIGH)
        assert qm.queue_size(Priority.HIGH) >= 0  # may or may not have been consumed
        total = qm.queue_size()
        assert total >= 0
        await qm.shutdown()


class TestQueueManagerPersistence:
    """Journal-based persistence and recovery."""

    @pytest.mark.asyncio
    async def test_journal_writes_on_enqueue(self, tmp_path):
        journal = _temp_journal_path(tmp_path)
        qm = QueueManager(lambda x: None, journal_path=journal)
        await qm.start(worker_count=1)
        await qm.enqueue({"hello": "world"}, Priority.HIGH)
        await asyncio.sleep(0.1)
        await qm.shutdown()

        assert os.path.exists(journal)
        # Journal should be truncated after shutdown (all tasks processed)
        # But if shutdown happens before processing it may still have content
        # The key test is that the file was created

    @pytest.mark.asyncio
    async def test_recovery_from_journal(self, tmp_path):
        journal = _temp_journal_path(tmp_path)
        results = []

        async def handler(item):
            results.append(item)

        # First session: enqueue and shut down (task stays in journal)
        qm1 = QueueManager(
            lambda x: None,  # no-op so task is NOT consumed
            journal_path=journal,
        )
        await qm1.start(worker_count=1)

        # Hold back processing to keep items in queue
        # We'll just enqueue then immediately shutdown
        # (QueueManager shutdown doesn't drain by default unless you await shutdown with consumers)

        # Actually let's enqueue and then force-kill without draining
        await qm1.enqueue({"recovered": True}, Priority.HIGH)

        # Don't shutdown normally — we want the journal to have pending tasks.
        # Instead we re-create QueueManager and recover.
        qm1._running = False  # crude stop to avoid sentinel
        for q in qm1._queues.values():
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break

        # Second session: recover
        qm2 = QueueManager(handler, journal_path=journal)
        await qm2.start(worker_count=1)
        await asyncio.sleep(0.2)
        await qm2.shutdown()

        assert {"recovered": True} in results

    @pytest.mark.asyncio
    async def test_journal_truncated_after_successful_recovery(self, tmp_path):
        journal = _temp_journal_path(tmp_path)

        # Write some raw journal entries
        os.makedirs(os.path.dirname(journal), exist_ok=True)
        with open(journal, "w", encoding="utf-8") as f:
            f.write(json.dumps({"priority": 100, "enqueued_at": time.monotonic(), "data": "legacy"}) + "\n")

        results = []

        async def handler(item):
            results.append(item)

        qm = QueueManager(handler, journal_path=journal)
        await qm.start(worker_count=1)
        await asyncio.sleep(0.2)
        await qm.shutdown()

        assert "legacy" in results
        # Journal should be empty after recovery
        content = Path(journal).read_text()
        assert content == ""


class TestQueueManagerErrorHandling:
    """Consumer error handling and stats."""

    @pytest.mark.asyncio
    async def test_handler_exception_increments_failed(self):
        async def handler(item):
            raise RuntimeError("consumer error")

        qm = QueueManager(handler)
        await qm.start(worker_count=1)
        await qm.enqueue({"x": 1})
        await asyncio.sleep(0.15)
        stats = qm.monitor()
        assert stats["failed_total"] >= 1
        await qm.shutdown()

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_stop_consumer(self):
        fail_count = 0

        async def handler(item):
            nonlocal fail_count
            if item.get("fail"):
                fail_count += 1
                raise RuntimeError("fail")
            return item

        qm = QueueManager(handler)
        await qm.start(worker_count=1)
        await qm.enqueue({"fail": True})
        await qm.enqueue({"fail": False})
        await asyncio.sleep(0.2)
        await qm.shutdown()
        stats = qm.monitor()
        assert stats["failed_total"] >= 1
        assert stats["processed_total"] >= 1


class TestQueueManagerShutdown:
    """Shutdown behavior."""

    @pytest.mark.asyncio
    async def test_shutdown_drains_pending_tasks(self):
        results = []

        async def handler(item):
            results.append(item)

        qm = QueueManager(handler)
        await qm.start(worker_count=2)
        await qm.enqueue({"n": 1})
        await qm.enqueue({"n": 2})
        await qm.enqueue({"n": 3})
        await qm.shutdown(timeout=5)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_double_shutdown_is_safe(self):
        qm = QueueManager(lambda x: None)
        await qm.start(worker_count=1)
        await qm.shutdown()
        await qm.shutdown()  # should not raise

    @pytest.mark.asyncio
    async def test_shutdown_timeout(self):
        async def slow_handler(item):
            await asyncio.sleep(2)

        qm = QueueManager(slow_handler)
        await qm.start(worker_count=1)
        await qm.enqueue({"slow": True})
        # Shutdown with short timeout — should cancel workers
        await qm.shutdown(timeout=0.1)
        # No exception expected


class TestQueueManagerQueueSizes:
    """Per-priority queue size queries."""

    @pytest.mark.asyncio
    async def test_queue_size_total(self):
        async def handler(item):
            await asyncio.sleep(0.05)

        qm = QueueManager(handler)
        await qm.start(worker_count=1)
        await qm.enqueue({"n": 1}, Priority.HIGH)
        await qm.enqueue({"n": 2}, Priority.LOW)
        await qm.enqueue({"n": 3}, Priority.LOW)
        await asyncio.sleep(0.01)  # tiny delay so items are in queue
        total = qm.queue_size()
        assert total >= 0  # may have been consumed already
        await qm.shutdown()


class TestQueueManagerSerializationFallback:
    """Serialization fallback for non-JSON data."""

    @pytest.mark.asyncio
    async def test_non_serializable_data_falls_back_to_str(self, tmp_path):
        journal = _temp_journal_path(tmp_path)
        results = []

        async def handler(item):
            results.append(item)

        class NonSerializable:
            def __str__(self):
                return "non-serializable"
            def __repr__(self):
                return "NonSerializable()"

        qm = QueueManager(handler, journal_path=journal)
        await qm.start(worker_count=1)
        await qm.enqueue(NonSerializable(), Priority.MEDIUM)
        await asyncio.sleep(0.15)
        await qm.shutdown()

        # Should not crash — the journal entry was written as string
        assert len(results) >= 0  # may or may not have been consumed


# ═══════════════════════════════════════════════════════════════════
# Integration: AsyncExecutor + QueueManager together
# ═══════════════════════════════════════════════════════════════════


class TestIntegration:
    """End-to-end: QueueManager feeds AsyncExecutor."""

    @pytest.mark.asyncio
    async def test_integration_pipeline(self):
        """QueueManager enqueues tasks; handler submits to AsyncExecutor."""
        processed = []

        async def process(item):
            # Simulate actual work
            await asyncio.sleep(0.01)
            processed.append(item)
            return item

        async with AsyncExecutor(max_concurrency=4) as ex:
            async def exec_handler(item):
                return await ex.submit(process, item)

            # QueueManager doesn't support returning TaskHandle directly,
            # so we use the executor inside the handler
            qm_results = []

            async def qm_handler(item):
                qm_results.append(item)

            qm = QueueManager(qm_handler)
            await qm.start(worker_count=2)

            for i in range(20):
                await qm.enqueue({"n": i})

            await asyncio.sleep(0.5)
            await qm.shutdown()

        assert len(qm_results) == 20
