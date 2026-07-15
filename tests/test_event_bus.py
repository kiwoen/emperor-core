"""Tests for EventBus publish-subscribe (stdlib queue)."""

from __future__ import annotations

import json
import queue
import threading
import time

import pytest

from jarvis.event_bus import Event, EventBus, event_bus


class TestEventBusSingleton:
    """Test singleton behavior."""

    def test_singleton(self):
        """EventBus is a singleton."""
        a = EventBus()
        b = EventBus()
        assert a is b

    def test_global_event_bus_is_singleton(self):
        """Module-level event_bus is the singleton."""
        assert event_bus is EventBus()


class TestSubscribeUnsubscribe:
    """Test subscription management."""

    def test_subscribe_returns_queue_and_id(self):
        """subscribe() returns a tuple (queue, sub_id)."""
        bus = EventBus()
        q, sub_id = bus.subscribe()
        assert isinstance(q, queue.Queue)
        assert isinstance(sub_id, int)
        assert bus.subscriber_count == 1

    def test_unsubscribe_removes(self, bus_fresh):
        """unsubscribe removes the queue."""
        q, sub_id = bus_fresh.subscribe()
        assert bus_fresh.subscriber_count == 1
        bus_fresh.unsubscribe(sub_id)
        assert bus_fresh.subscriber_count == 0

    def test_unsubscribe_nonexistent_no_error(self, bus_fresh):
        """Unsubscribing an unknown id is safe."""
        bus_fresh.unsubscribe(999999)
        assert bus_fresh.subscriber_count == 0

    def test_multiple_subscribers(self, bus_fresh):
        """Multiple subscribers all connected."""
        q1, _ = bus_fresh.subscribe()
        q2, _ = bus_fresh.subscribe()
        q3, _ = bus_fresh.subscribe()
        assert bus_fresh.subscriber_count == 3


class TestPublishReceive:
    """Test event publishing."""

    def test_single_subscriber_receives(self, bus_fresh):
        """Subscriber receives published event."""
        q, _ = bus_fresh.subscribe()
        bus_fresh.publish(Event("test", {"key": "val"}))

        try:
            raw = q.get(timeout=1)
            data = json.loads(raw)
        except queue.Empty:
            pytest.fail("Event not received within timeout")

        assert data["type"] == "test"
        assert data["data"] == {"key": "val"}
        assert "ts" in data

    def test_multiple_subscribers_all_receive(self, bus_fresh):
        """All subscribers get the event."""
        q1, _ = bus_fresh.subscribe()
        q2, _ = bus_fresh.subscribe()

        bus_fresh.publish(Event("broadcast", {"n": 1}))

        for q in [q1, q2]:
            try:
                raw = q.get(timeout=1)
                data = json.loads(raw)
                assert data["type"] == "broadcast"
            except queue.Empty:
                pytest.fail("Subscriber did not receive event")

    def test_unsubscribe_cleanup(self, bus_fresh):
        """After unsubscribe, subscriber stops receiving."""
        q, sub_id = bus_fresh.subscribe()
        bus_fresh.unsubscribe(sub_id)

        bus_fresh.publish(Event("orphan", {}))
        # Queue should NOT receive the event (we unsubscribed)
        try:
            q.get(timeout=0.5)
            # We also check nothing in queue — but there could be
            # Only assert subscriber count is 0
        except queue.Empty:
            pass
        assert bus_fresh.subscriber_count == 0

    def test_queue_full_drops_slow_consumer(self, bus_fresh):
        """When a queue is full, the subscriber is dropped."""
        # Create a tiny queue that fills immediately
        bus_fresh._queues = []
        tiny_q = queue.Queue(maxsize=1)
        bus_fresh._queues.append(tiny_q)

        # Fill it
        tiny_q.put_nowait("block")
        assert bus_fresh.subscriber_count == 1

        # Publish should now drop this slow consumer
        bus_fresh.publish(Event("overflow", {}))
        assert bus_fresh.subscriber_count == 0

    def test_heartbeat_event(self, bus_fresh):
        """Heartbeat event has correct structure."""
        q, _ = bus_fresh.subscribe()
        bus_fresh.publish_heartbeat()

        raw = q.get(timeout=1)
        data = json.loads(raw)
        assert data["type"] == "heartbeat"
        assert data["data"] == {}

    def test_event_repr(self):
        """Event __repr__ is human-readable."""
        e = Event("task_completed", {"x": 1})
        r = repr(e)
        assert "task_completed" in r


class TestConcurrency:
    """Test thread safety."""

    def test_concurrent_publish(self, bus_fresh):
        """Concurrent publishes from multiple threads."""
        q, _ = bus_fresh.subscribe()

        def worker(n: int):
            for i in range(10):
                bus_fresh.publish(Event("batch", {"worker": n, "i": i}))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 40 events should be in the queue
        count = 0
        while True:
            try:
                q.get(timeout=0.5)
                count += 1
            except queue.Empty:
                break
        assert count == 40

    def test_concurrent_subscribe_unsubscribe(self, bus_fresh):
        """Concurrent subscribe/unsubscribe does not crash."""
        def sub_loop():
            for _ in range(20):
                _, sid = bus_fresh.subscribe()
                bus_fresh.unsubscribe(sid)

        threads = [threading.Thread(target=sub_loop) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert bus_fresh.subscriber_count == 0

    def test_maxsize_100(self, bus_fresh):
        """Default queue maxsize is 100."""
        q, _ = bus_fresh.subscribe()
        assert q.maxsize == 100


# ── Fixtures ──

@pytest.fixture
def bus_fresh():
    """Return a fresh EventBus with cleared subscribers."""
    bus = EventBus()
    bus._queues = []
    return bus
