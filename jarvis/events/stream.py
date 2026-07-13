"""
EventStreamManager — bridges Hermes MessageBus to WebSocket clients.

Routes bus messages matching topic subscriptions to connected clients.
Supports wildcard topic patterns (e.g. 'codex.*', 'vscode.*', '*').
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("jarvis.events")


@dataclass
class EventClient:
    """Represents a connected streaming client."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    send_json: Any = field(default=None, repr=False)  # callable: async fn(dict) -> None
    subscriptions: set[str] = field(default_factory=set)


class EventStreamManager:
    """Manages WebSocket event streaming from Hermes bus to connected clients.

    Usage:
        manager = EventStreamManager(bus)
        await manager.start()

        client_id = "ws-abc123"
        await manager.register(client_id, websocket.send_json)
        await manager.subscribe(client_id, "codex.*")

        # Now codex bus messages automatically reach this WebSocket client.

        await manager.stop()
    """

    def __init__(self, bus: Any) -> None:
        """Initialize with a Hermes MessageBus instance.

        Args:
            bus: A jarvis.hermes.bus.MessageBus instance.
        """
        self._bus = bus
        self._clients: dict[str, EventClient] = {}
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        """Subscribe to wildcard on the bus and begin bridging events."""
        if self._started:
            return

        self._bus.subscribe(self._on_bus_message, topic_pattern="*")
        self._started = True
        logger.info("EventStreamManager started — listening on '*'")

    async def stop(self) -> None:
        """Stop bridging. Clients are NOT disconnected — connections persist."""
        self._started = False
        logger.info("EventStreamManager stopped")

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    async def register(self, client_id: str, send_json: Any) -> None:
        """Register a WebSocket client for event streaming.

        Args:
            client_id: Unique identifier for this client.
            send_json: Async callable that sends JSON to the WebSocket
                       (typically websocket.send_json).
        """
        if client_id in self._clients:
            logger.debug("Client %s already registered — reusing", client_id)
        self._clients[client_id] = EventClient(
            id=client_id,
            send_json=send_json,
        )
        logger.debug("Client %s registered (%d total)", client_id, len(self._clients))

    async def unregister(self, client_id: str) -> None:
        """Unregister a client and clean up its subscriptions."""
        self._clients.pop(client_id, None)
        logger.debug("Client %s unregistered (%d total)", client_id, len(self._clients))

    # ------------------------------------------------------------------
    # Topic subscriptions
    # ------------------------------------------------------------------

    async def subscribe(self, client_id: str, topic_pattern: str) -> bool:
        """Subscribe a client to a topic pattern (supports fnmatch wildcards).

        Returns True if added, False if already present.
        """
        client = self._clients.get(client_id)
        if client is None:
            logger.warning("subscribe called for unknown client %s", client_id)
            return False

        before = len(client.subscriptions)
        client.subscriptions.add(topic_pattern)
        added = len(client.subscriptions) > before
        if added:
            logger.debug("Client %s subscribed to '%s'", client_id, topic_pattern)
        return added

    async def unsubscribe(self, client_id: str, topic_pattern: Optional[str] = None) -> int:
        """Unsubscribe from a specific topic pattern, or all if None.

        Returns the number of patterns removed.
        """
        client = self._clients.get(client_id)
        if client is None:
            return 0

        if topic_pattern is None:
            count = len(client.subscriptions)
            client.subscriptions.clear()
            return count

        before = len(client.subscriptions)
        client.subscriptions.discard(topic_pattern)
        return before - len(client.subscriptions)

    async def get_client_subscriptions(self, client_id: str) -> list[str]:
        """Return all topic patterns a client is subscribed to."""
        client = self._clients.get(client_id)
        if client is None:
            return []
        return sorted(client.subscriptions)

    # ------------------------------------------------------------------
    # Subscription graph (for status / debugging)
    # ------------------------------------------------------------------

    def subscription_graph(self) -> dict[str, list[str]]:
        """Return the current subscription graph: {client_id: [patterns]}."""
        return {
            cid: sorted(client.subscriptions)
            for cid, client in self._clients.items()
        }

    def status(self) -> dict:
        """Return health-check summary."""
        return {
            "started": self._started,
            "clients": len(self._clients),
            "total_subscriptions": sum(
                len(c.subscriptions) for c in self._clients.values()
            ),
            "graph": self.subscription_graph(),
        }

    # ------------------------------------------------------------------
    # Internal: bus message handler
    # ------------------------------------------------------------------

    async def _on_bus_message(self, message: Any) -> None:
        """Route a bus message to all clients with matching topic subscriptions."""
        if not self._started:
            return

        topic_path = str(message.topic) if hasattr(message, "topic") else "unknown"

        for client_id, client in list(self._clients.items()):
            # Check if client subscribes to any pattern matching this topic
            matched = any(
                fnmatch.fnmatch(topic_path, pattern)
                for pattern in client.subscriptions
            )
            if not matched:
                continue

            try:
                event = self._serialize(message)
                await client.send_json(event)
            except Exception:
                logger.debug(
                    "Failed to send event to client %s (topic=%s)",
                    client_id,
                    topic_path,
                )

    def _serialize(self, message: Any) -> dict:
        """Convert a Hermes Message to a JSON-serializable event dict."""
        payload = message.payload
        if not isinstance(payload, (dict, list, str, int, float, bool, type(None))):
            payload = str(payload)

        return {
            "type": "bus_event",
            "message_id": str(message.id) if hasattr(message, "id") else "",
            "topic": str(message.topic) if hasattr(message, "topic") else "",
            "msg_type": str(message.type.value) if hasattr(message, "type") else "event",
            "payload": payload,
            "sender": str(message.sender) if hasattr(message, "sender") else "",
            "timestamp": (
                datetime.fromtimestamp(message.timestamp, tz=timezone.utc).isoformat()
                if hasattr(message, "timestamp")
                else datetime.now(timezone.utc).isoformat()
            ),
        }
