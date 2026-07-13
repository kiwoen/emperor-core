"""
Tests for EventStreamManager — Hermes bus → WebSocket event bridging.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from jarvis.events.stream import EventStreamManager, EventClient
from jarvis.hermes.bus import MessageBus, Message, Topic, MessageType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bus() -> MessageBus:
    return MessageBus()


@pytest.fixture
def manager(bus: MessageBus) -> EventStreamManager:
    return EventStreamManager(bus)


# ---------------------------------------------------------------------------
# Fake WebSocket — records sent messages for assertion
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """Records sent JSON messages for test verification."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    @pytest.mark.asyncio
    async def test_register_adds_client(self, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        assert manager.client_count == 1

    @pytest.mark.asyncio
    async def test_register_duplicate_id_is_noop(self, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.register("client-1", ws.send_json)
        assert manager.client_count == 1

    @pytest.mark.asyncio
    async def test_unregister_removes_client(self, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.unregister("client-1")
        assert manager.client_count == 0

    @pytest.mark.asyncio
    async def test_unregister_unknown_client_does_not_crash(self, manager: EventStreamManager):
        await manager.unregister("nonexistent")  # no exception

    @pytest.mark.asyncio
    async def test_multiple_clients(self, manager: EventStreamManager):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await manager.register("a", ws1.send_json)
        await manager.register("b", ws2.send_json)
        assert manager.client_count == 2


# ---------------------------------------------------------------------------
# Topic subscriptions
# ---------------------------------------------------------------------------

class TestSubscriptions:
    @pytest.mark.asyncio
    async def test_subscribe_adds_pattern(self, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.subscribe("client-1", "codex.*")
        subs = await manager.get_client_subscriptions("client-1")
        assert "codex.*" in subs

    @pytest.mark.asyncio
    async def test_subscribe_unknown_client_returns_false(self, manager: EventStreamManager):
        result = await manager.subscribe("ghost", "codex.*")
        assert result is False

    @pytest.mark.asyncio
    async def test_unsubscribe_single_pattern(self, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.subscribe("client-1", "codex.*")
        await manager.subscribe("client-1", "vscode.*")

        removed = await manager.unsubscribe("client-1", "codex.*")
        assert removed == 1
        subs = await manager.get_client_subscriptions("client-1")
        assert "codex.*" not in subs
        assert "vscode.*" in subs

    @pytest.mark.asyncio
    async def test_unsubscribe_all(self, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.subscribe("client-1", "codex.*")
        await manager.subscribe("client-1", "vscode.*")

        removed = await manager.unsubscribe("client-1")  # None = all
        assert removed == 2
        subs = await manager.get_client_subscriptions("client-1")
        assert subs == []

    @pytest.mark.asyncio
    async def test_unsubscribe_unknown_client_returns_zero(self, manager: EventStreamManager):
        assert await manager.unsubscribe("ghost") == 0

    @pytest.mark.asyncio
    async def test_duplicate_subscribe_is_idempotent(self, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        r1 = await manager.subscribe("client-1", "codex.*")
        r2 = await manager.subscribe("client-1", "codex.*")
        assert r1 is True
        assert r2 is False
        subs = await manager.get_client_subscriptions("client-1")
        assert subs == ["codex.*"]

    @pytest.mark.asyncio
    async def test_clear_subscriptions(self, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.subscribe("client-1", "codex.*")
        await manager.subscribe("client-1", "vscode.*")
        await manager.unsubscribe("client-1")  # clear all
        assert await manager.get_client_subscriptions("client-1") == []


# ---------------------------------------------------------------------------
# Bus message bridging
# ---------------------------------------------------------------------------

class TestMessageBridging:
    @pytest.mark.asyncio
    async def test_event_reaches_subscribed_client(self, bus: MessageBus, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.subscribe("client-1", "codex.*")
        await manager.start()

        msg = Message(
            topic=Topic("codex.review.completed"),
            type=MessageType.EVENT,
            payload={"score": 8.5, "issues": 2},
            sender="codex-engine",
        )
        await bus.publish(msg)

        # Give async bridging a moment
        await asyncio.sleep(0.05)

        assert len(ws.sent) >= 1
        event = ws.sent[-1]
        assert event["type"] == "bus_event"
        assert event["topic"] == "codex.review.completed"
        assert event["payload"] == {"score": 8.5, "issues": 2}

    @pytest.mark.asyncio
    async def test_event_not_sent_to_unsubscribed_client(self, bus: MessageBus, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        # No subscriptions — subscribe to nothing
        await manager.start()

        msg = Message(topic=Topic("system.alert"), type=MessageType.SYSTEM)
        await bus.publish(msg)
        await asyncio.sleep(0.05)

        assert len(ws.sent) == 0

    @pytest.mark.asyncio
    async def test_wildcard_matching(self, bus: MessageBus, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.subscribe("client-1", "vscode.*")
        await manager.start()

        # Should match
        msg1 = Message(topic=Topic("vscode.file.opened"), type=MessageType.EVENT, payload={"file": "app.py"})
        await bus.publish(msg1)

        # Should NOT match
        msg2 = Message(topic=Topic("codex.review.done"), type=MessageType.EVENT)
        await bus.publish(msg2)

        await asyncio.sleep(0.05)

        assert len(ws.sent) == 1
        assert ws.sent[0]["topic"] == "vscode.file.opened"

    @pytest.mark.asyncio
    async def test_multiple_clients_isolated(self, bus: MessageBus, manager: EventStreamManager):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await manager.register("a", ws1.send_json)
        await manager.register("b", ws2.send_json)
        await manager.subscribe("a", "codex.*")
        await manager.subscribe("b", "vscode.*")
        await manager.start()

        msg = Message(topic=Topic("codex.review.done"), type=MessageType.EVENT)
        await bus.publish(msg)
        await asyncio.sleep(0.05)

        assert len(ws1.sent) >= 1  # a gets it
        assert len(ws2.sent) == 0  # b does not

    @pytest.mark.asyncio
    async def test_client_disconnect_no_crash(self, bus: MessageBus, manager: EventStreamManager):
        """After a client is unregistered, bus messages should not crash."""
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.subscribe("client-1", "codex.*")
        await manager.start()

        # Unregister client while bus is active
        await manager.unregister("client-1")

        msg = Message(topic=Topic("codex.review.done"), type=MessageType.EVENT)
        await bus.publish(msg)
        await asyncio.sleep(0.05)

        # Should not crash — previous sent messages may exist, that's fine
        assert manager.client_count == 0


# ---------------------------------------------------------------------------
# Status & Graph
# ---------------------------------------------------------------------------

class TestStatus:
    @pytest.mark.asyncio
    async def test_status_before_start(self, manager: EventStreamManager):
        s = manager.status()
        assert s["started"] is False
        assert s["clients"] == 0

    @pytest.mark.asyncio
    async def test_status_after_start(self, manager: EventStreamManager):
        await manager.start()
        s = manager.status()
        assert s["started"] is True

    @pytest.mark.asyncio
    async def test_subscription_graph(self, manager: EventStreamManager):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await manager.register("a", ws1.send_json)
        await manager.register("b", ws2.send_json)
        await manager.subscribe("a", "codex.*")
        await manager.subscribe("b", "vscode.*")

        graph = manager.subscription_graph()
        assert list(graph.keys()) == ["a", "b"]
        assert "codex.*" in graph["a"]
        assert "vscode.*" in graph["b"]

    @pytest.mark.asyncio
    async def test_started_property(self, manager: EventStreamManager):
        assert manager.started is False
        await manager.start()
        assert manager.started is True


# ---------------------------------------------------------------------------
# Serialization edge cases
# ---------------------------------------------------------------------------

class TestSerialization:
    @pytest.mark.asyncio
    async def test_non_dict_payload_is_stringified(self, bus: MessageBus, manager: EventStreamManager):
        ws = FakeWebSocket()
        await manager.register("client-1", ws.send_json)
        await manager.subscribe("client-1", "*")
        await manager.start()

        class CustomObj:
            def __str__(self):
                return "custom-object"

        msg = Message(topic=Topic("test.payload"), type=MessageType.EVENT, payload=CustomObj())
        await bus.publish(msg)
        await asyncio.sleep(0.05)

        assert len(ws.sent) >= 1
        assert ws.sent[-1]["payload"] == "custom-object"
