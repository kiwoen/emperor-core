"""Simple publish-subscribe event bus (stdlib-only, zero deps)."""

from __future__ import annotations

import json
import queue
import threading
import time


class Event:
    """A lightweight event envelope."""

    __slots__ = ("type", "data", "timestamp")

    def __init__(self, event_type: str, data: dict) -> None:
        self.type = event_type
        self.data = data
        self.timestamp = time.time()

    def __repr__(self) -> str:
        return f"Event(type={self.type!r}, ts={self.timestamp:.3f})"


class EventBus:
    """Thread-safe publish-subscribe bus backed by queue.Queue.

    - subscribe()  → (Queue, sub_id)
    - unsubscribe(sub_id) removes the queue
    - publish(event) pushes JSON-serialized event to every subscriber
    - publish_heartbeat() sends a heartbeat event (useful for SSE keep-alive)

    Each subscriber queue has maxsize=100; slow consumers are automatically
    dropped when the queue is full.
    """

    _instance: EventBus | None = None

    def __new__(cls) -> EventBus:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._queues: list[queue.Queue] = []
            cls._instance._lock = threading.Lock()
        return cls._instance

    def subscribe(self) -> tuple[queue.Queue, int]:
        """Create a new subscriber queue, return (queue, subscription_id)."""
        q: queue.Queue = queue.Queue(maxsize=100)
        sub_id = id(q)
        with self._lock:
            self._queues.append(q)
        return q, sub_id

    def unsubscribe(self, sub_id: int) -> None:
        """Remove a subscriber by its subscription id."""
        with self._lock:
            self._queues = [q for q in self._queues if id(q) != sub_id]

    def publish(self, event: Event) -> None:
        """Broadcast event to all subscribers as a JSON string."""
        data = json.dumps(
            {"type": event.type, "data": event.data, "ts": event.timestamp},
            ensure_ascii=False,
        )
        with self._lock:
            dead: list[queue.Queue] = []
            for q in self._queues:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._queues.remove(q)

    def publish_heartbeat(self) -> None:
        """Publish a heartbeat event (used to keep SSE connections alive)."""
        self.publish(Event("heartbeat", {}))

    @property
    def subscriber_count(self) -> int:
        """Return the number of active subscribers (mainly for tests)."""
        with self._lock:
            return len(self._queues)


# Convenience global singleton
event_bus = EventBus()
