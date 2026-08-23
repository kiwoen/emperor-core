"""Tests for Hermes message bus and event log."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from jarvis.hermes.bus import Message, MessageBus, MessageType, Topic
from jarvis.hermes.event_log import EventLog


# ============================================================================
# Topic
# ============================================================================

class TestTopic:
    def test_simple_match(self):
        t = Topic("codex.review.completed")
        assert t.matches("codex.*")
        assert t.matches("codex.review.*")
        assert t.matches("codex.review.completed")
        assert not t.matches("vscode.*")

    def test_wildcard_match(self):
        t = Topic("orchestrator.intent.parsed")
        assert t.matches("orchestrator.*")
        assert t.matches("*.intent.*")
        assert t.matches("*.*.parsed")
        assert t.matches("*")

    def test_normalization(self):
        t = Topic(".codex..review.")
        assert t.path == "codex.review"

    def test_hash(self):
        t1 = Topic("a.b")
        t2 = Topic("a.b")
        assert hash(t1) == hash(t2)
        assert {t1, Topic("x.y")} == {t2, Topic("x.y")}


# ============================================================================
# Message
# ============================================================================

class TestMessage:
    def test_defaults(self):
        msg = Message(topic=Topic("test.ping"))
        assert msg.type == MessageType.EVENT
        assert len(msg.id) == 12
        assert msg.payload is None

    def test_reply_chain(self):
        req = Message(topic=Topic("codex.generate"), type=MessageType.REQUEST, payload="code")
        reply = req.reply(payload="result", sender="codex")
        assert reply.type == MessageType.REPLY
        assert reply.correlation_id == req.id
        assert reply.payload == "result"


# ============================================================================
# MessageBus: Pub/Sub
# ============================================================================

class TestMessageBusPubSub:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        bus = MessageBus()
        received: list[Message] = []

        async def handler(msg):
            received.append(msg)

        bus.subscribe(handler, "test.*")
        await bus.publish(Message(topic=Topic("test.event"), payload="hello"))
        await asyncio.sleep(0.01)

        assert len(received) == 1
        assert received[0].payload == "hello"

    @pytest.mark.asyncio
    async def test_pattern_filtering(self):
        bus = MessageBus()
        codex_msgs: list[Message] = []
        all_msgs: list[Message] = []

        async def codex_handler(msg):
            codex_msgs.append(msg)

        async def catch_all(msg):
            all_msgs.append(msg)

        bus.subscribe(codex_handler, "codex.*")
        bus.subscribe(catch_all, "*")

        await bus.publish(Message(topic=Topic("codex.review")))
        await bus.publish(Message(topic=Topic("vscode.open")))

        assert len(codex_msgs) == 1
        assert codex_msgs[0].topic.path == "codex.review"
        assert len(all_msgs) == 2

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = MessageBus()
        received: list[Message] = []

        async def handler(msg):
            received.append(msg)

        sub = bus.subscribe(handler, "test.*")
        assert bus.subscriber_count == 1
        bus.unsubscribe(sub.id)
        assert bus.subscriber_count == 0

        await bus.publish(Message(topic=Topic("test.event")))
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_once_subscription(self):
        bus = MessageBus()
        received: list[Message] = []

        async def handler(msg):
            received.append(msg)

        bus.subscribe(handler, "test.*", once=True)

        await bus.publish(Message(topic=Topic("test.a")))
        await bus.publish(Message(topic=Topic("test.b")))

        assert len(received) == 1
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_pattern(self):
        bus = MessageBus()
        results: list[str] = []

        async def h1(msg):
            results.append("h1")

        async def h2(msg):
            results.append("h2")

        bus.subscribe(h1, "test.*")
        bus.subscribe(h2, "test.*")

        await bus.publish(Message(topic=Topic("test.event")))
        assert len(results) == 2
        assert "h1" in results
        assert "h2" in results

    @pytest.mark.asyncio
    async def test_topic_summary(self):
        bus = MessageBus()

        async def noop(msg):
            pass

        bus.subscribe(noop, "codex.*")
        bus.subscribe(noop, "vscode.*")
        bus.subscribe(noop, "codex.review")

        summary = bus.topic_summary()
        assert summary["codex.*"] == 1
        assert summary["vscode.*"] == 1
        assert summary["codex.review"] == 1


# ============================================================================
# MessageBus: Request/Reply
# ============================================================================

class TestMessageBusRequestReply:
    @pytest.mark.asyncio
    async def test_request_reply(self):
        bus = MessageBus()

        async def server(msg):
            await bus.reply(msg, payload=f"echo: {msg.payload}", sender="server")

        bus.subscribe(server, "echo.request")

        reply = await bus.request(Topic("echo.request"), payload="hello", timeout=2.0)
        assert reply.payload == "echo: hello"

    @pytest.mark.asyncio
    async def test_request_timeout(self):
        bus = MessageBus()

        # No subscriber → RuntimeError
        with pytest.raises(RuntimeError, match="No subscribers"):
            await bus.request(Topic("no.subscriber"), payload="ping", timeout=0.5)

    @pytest.mark.asyncio
    async def test_request_timeout_slow_handler(self):
        bus = MessageBus()

        async def slow(msg):
            await asyncio.sleep(5)  # never replies in time

        bus.subscribe(slow, "slow.*")

        with pytest.raises(asyncio.TimeoutError):
            await bus.request(Topic("slow.op"), payload="go", timeout=0.2)

    @pytest.mark.asyncio
    async def test_request_no_subscriber_raises(self):
        bus = MessageBus()
        with pytest.raises(RuntimeError, match="No subscribers"):
            await bus.request(Topic("ghost.topic"), payload="x")


# ============================================================================
# MessageBus: Lifecycle
# ============================================================================

class TestMessageBusLifecycle:
    @pytest.mark.asyncio
    async def test_start_shutdown(self):
        bus = MessageBus()
        await bus.start()
        assert bus.message_count == 0

        async def noop(msg):
            pass

        bus.subscribe(noop, "test.*")
        await bus.publish(Message(topic=Topic("test.event")))
        assert bus.message_count == 1

        await bus.shutdown()
        assert bus.subscriber_count == 0


# ============================================================================
# EventLog
# ============================================================================

class TestEventLog:
    @pytest.fixture
    def log_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield d

    @pytest.mark.asyncio
    async def test_append_and_tail(self, log_dir):
        log = EventLog(str(Path(log_dir) / "events.log"))
        await log.start()

        msg = Message(topic=Topic("test.event"), payload="hello")
        log.append(msg)

        await asyncio.sleep(0.6)  # wait for writer flush
        await log.shutdown()

        events = log.tail(10)
        assert len(events) == 1
        assert events[0]["topic"] == "test.event"
        assert events[0]["payload"] == "hello"

    @pytest.mark.asyncio
    async def test_replay_filtered(self, log_dir):
        log = EventLog(str(Path(log_dir) / "events.log"))
        await log.start()

        for i in range(5):
            msg = Message(topic=Topic(f"codex.event.{i}"), payload=i)
            log.append(msg)

        await asyncio.sleep(0.6)
        await log.shutdown()

        filtered = log.replay("codex.*")
        assert len(filtered) == 5

    @pytest.mark.asyncio
    async def test_integration_bus_with_log(self, log_dir):
        """End-to-end: bus publishes → event log records."""
        log = EventLog(str(Path(log_dir) / "events.log"))
        await log.start()

        bus = MessageBus(event_log=log)

        async def handler(msg):
            pass

        bus.subscribe(handler, "codex.*")
        await bus.publish(Message(topic=Topic("codex.generate"), payload="def foo(): pass"))

        await asyncio.sleep(0.6)
        await bus.shutdown()
        await log.shutdown()

        events = log.tail(10)
        assert len(events) >= 1
        assert any(e["topic"] == "codex.generate" for e in events)


# ============================================================================
# Concurrent stress
# ============================================================================

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_publish(self):
        bus = MessageBus()
        received: list[Message] = []

        async def handler(msg):
            received.append(msg)
            await asyncio.sleep(0)  # yield

        bus.subscribe(handler, "burst.*")

        tasks = [
            bus.publish(Message(topic=Topic(f"burst.{i}"), payload=i))
            for i in range(100)
        ]
        await asyncio.gather(*tasks)

        assert len(received) == 100
        assert sorted(m.payload for m in received) == list(range(100))
