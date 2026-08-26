"""Tests for EventBus publishing and real-time event integration."""

import pytest
import json
import time
import queue
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_event_bus():
    """Reset EventBus singleton to clean state before each test."""
    from huanxin.event_bus import EventBus, event_bus
    # Clear all subscribers
    with event_bus._lock:
        event_bus._queues.clear()
    yield
    with event_bus._lock:
        event_bus._queues.clear()


@pytest.fixture
def subscriber():
    """Create a subscriber and return (queue, sub_id)."""
    from huanxin.event_bus import event_bus
    q, sub_id = event_bus.subscribe()
    yield q, sub_id
    try:
        event_bus.unsubscribe(sub_id)
    except Exception:
        pass


# ── Event Model ───────────────────────────────────────────────────

class TestEvent:
    def test_event_creation(self):
        from huanxin.event_bus import Event
        e = Event("test", {"key": "val"})
        assert e.type == "test"
        assert e.data == {"key": "val"}
        assert e.timestamp > 0

    def test_event_slots(self):
        from huanxin.event_bus import Event
        e = Event("t", {})
        with pytest.raises(AttributeError):
            e.new_attr = 1


# ── EventBus Core ─────────────────────────────────────────────────

class TestEventBus:
    def test_singleton(self):
        from huanxin.event_bus import EventBus
        a = EventBus()
        b = EventBus()
        assert a is b

    def test_subscribe(self, subscriber):
        q, sub_id = subscriber
        assert isinstance(q, queue.Queue)
        assert isinstance(sub_id, int)

    def test_publish_receive(self, subscriber):
        from huanxin.event_bus import Event, event_bus
        q, _ = subscriber
        event_bus.publish(Event("dispatch", {"minister": "test"}))
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "dispatch"
        assert data["data"]["minister"] == "test"
        assert "ts" in data

    def test_unsubscribe_stops_delivery(self):
        from huanxin.event_bus import Event, event_bus
        q, sub_id = event_bus.subscribe()
        event_bus.unsubscribe(sub_id)
        event_bus.publish(Event("x", {}))
        with pytest.raises(queue.Empty):
            q.get(timeout=0.1)

    def test_subscriber_count(self):
        from huanxin.event_bus import event_bus
        initial = event_bus.subscriber_count
        q1, s1 = event_bus.subscribe()
        q2, s2 = event_bus.subscribe()
        assert event_bus.subscriber_count == initial + 2
        event_bus.unsubscribe(s1)
        assert event_bus.subscriber_count == initial + 1
        event_bus.unsubscribe(s2)
        assert event_bus.subscriber_count == initial

    def test_heartbeat(self, subscriber):
        from huanxin.event_bus import event_bus
        q, _ = subscriber
        event_bus.publish_heartbeat()
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "heartbeat"


# ── Event Publisher Helpers ───────────────────────────────────────

class TestPublisherDispatch:
    def test_publish_dispatch(self, subscriber):
        from huanxin.event_publisher import publish_dispatch
        q, _ = subscriber
        publish_dispatch("censor", "ed-001", "search web", True, 0.95, 123.4)
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "dispatch"
        d = data["data"]
        assert d["minister"] == "censor"
        assert d["edict_id"] == "ed-001"
        assert d["success"] is True
        assert d["confidence"] == 0.95
        assert d["elapsed_ms"] == 123.4

    def test_publish_dispatch_failure(self, subscriber):
        from huanxin.event_publisher import publish_dispatch
        q, _ = subscriber
        publish_dispatch("scribe", "ed-002", "write file", False, 0.1, 500.0)
        data = json.loads(q.get(timeout=1))
        assert data["data"]["success"] is False
        assert data["data"]["confidence"] == 0.1


class TestPublisherSandbox:
    def test_publish_sandbox(self, subscriber):
        from huanxin.event_publisher import publish_sandbox
        q, _ = subscriber
        publish_sandbox("print('hello')", 0, "local_subprocess", 45.2)
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "sandbox"
        d = data["data"]
        assert d["exit_code"] == 0
        assert "hello" in d["code_snippet"]
        assert d["engine"] == "local_subprocess"
        assert d["elapsed_ms"] == 45.2

    def test_publish_sandbox_long_code_truncated(self, subscriber):
        from huanxin.event_publisher import publish_sandbox
        q, _ = subscriber
        long_code = "print('" + "x" * 100 + "')"
        publish_sandbox(long_code, 1, "local_direct", 10.0)
        data = json.loads(q.get(timeout=1))
        assert len(data["data"]["code_snippet"]) <= 60


class TestPublisherPipeline:
    def test_publish_pipeline(self, subscriber):
        from huanxin.event_publisher import publish_pipeline
        q, _ = subscriber
        publish_pipeline("daily_brief", "pipe-001", "completed", steps=5, elapsed_ms=1500.0)
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "pipeline"
        d = data["data"]
        assert d["template"] == "daily_brief"
        assert d["status"] == "completed"
        assert d["steps"] == 5
        assert d["elapsed_ms"] == 1500.0


class TestPublisherGovernance:
    def test_publish_governance_create(self, subscriber):
        from huanxin.event_publisher import publish_governance_rule
        q, _ = subscriber
        publish_governance_rule("create", "no-delete-files", "P0", "Prevent file deletion")
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "governance"
        assert data["data"]["action"] == "create"
        assert data["data"]["priority"] == "P0"

    def test_publish_governance_delete(self, subscriber):
        from huanxin.event_publisher import publish_governance_rule
        q, _ = subscriber
        publish_governance_rule("delete", "old-rule")
        data = json.loads(q.get(timeout=1))
        assert data["data"]["action"] == "delete"


class TestPublisherHealing:
    def test_publish_healing_success(self, subscriber):
        from huanxin.event_publisher import publish_healing
        q, _ = subscriber
        publish_healing("restart_service", "success", "scheduler", 234.5)
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "healing"
        assert data["data"]["result"] == "success"
        assert data["data"]["triggered_by"] == "scheduler"

    def test_publish_healing_failure(self, subscriber):
        from huanxin.event_publisher import publish_healing
        q, _ = subscriber
        publish_healing("clear_cache", "failure")
        data = json.loads(q.get(timeout=1))
        assert data["data"]["result"] == "failure"


class TestPublisherApproval:
    def test_publish_approval_approved(self, subscriber):
        from huanxin.event_publisher import publish_approval
        q, _ = subscriber
        publish_approval("req-001", "approved", "high", True)
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "approval"
        assert data["data"]["approved"] is True
        assert data["data"]["risk_level"] == "high"

    def test_publish_approval_denied(self, subscriber):
        from huanxin.event_publisher import publish_approval
        q, _ = subscriber
        publish_approval("req-002", "denied", "low", False)
        data = json.loads(q.get(timeout=1))
        assert data["data"]["approved"] is False


class TestPublisherMemory:
    def test_publish_memory_consolidate(self, subscriber):
        from huanxin.event_publisher import publish_memory
        q, _ = subscriber
        publish_memory("consolidate", node_count=42)
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "memory"
        assert data["data"]["action"] == "consolidate"
        assert data["data"]["node_count"] == 42


class TestPublisherEval:
    def test_publish_eval(self, subscriber):
        from huanxin.event_publisher import publish_eval
        q, _ = subscriber
        publish_eval("governance", 10, 2, 500.0)
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "eval"
        assert data["data"]["passed"] == 10
        assert data["data"]["failed"] == 2
        assert data["data"]["total"] == 12
        assert data["data"]["total_ms"] == 500.0


class TestPublisherAlert:
    def test_publish_alert(self, subscriber):
        from huanxin.event_publisher import publish_alert
        q, _ = subscriber
        publish_alert("alert-1", "critical", "Disk usage > 95%", "system")
        data = json.loads(q.get(timeout=1))
        assert data["type"] == "alert"
        d = data["data"]
        assert d["alert_id"] == "alert-1"
        assert d["level"] == "critical"
        assert "95%" in d["message"]
        assert "ts" in d


# ── Integration: SSE endpoint publishes events ────────────────────

class TestSSEEventIntegration:
    @pytest.fixture
    def client(self):
        from huanxin.court_api import create_app
        from fastapi.testclient import TestClient
        app = create_app()
        return TestClient(app)

    @pytest.mark.skip(reason="SSE streaming not supported in TestClient; tested via EventBus directly")
    def test_sse_endpoint_streams_200(self, client):
        """SSE endpoint responds with 200 + text/event-stream."""
        with client.stream("GET", "/api/events") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_dispatch_endpoint_triggers_event(self, client):
        """Dispatch endpoint publishes events to EventBus."""
        from huanxin.event_bus import event_bus

        with event_bus._lock:
            event_bus._queues.clear()

        q, sub_id = event_bus.subscribe()

        try:
            resp = client.post("/court/dispatch", json={
                "minister": "censor",
                "edict_id": "ed-bus-test",
                "intent": "bus integration test",
                "success": True,
                "confidence": 0.99,
                "execution_time_ms": 100.0,
            })
            assert resp.status_code == 200

            import json
            data = json.loads(q.get(timeout=2))
            assert data["type"] == "dispatch"
            assert data["data"]["edict_id"] == "ed-bus-test"
        finally:
            event_bus.unsubscribe(sub_id)

    def test_sandbox_endpoint_triggers_event(self, client):
        """Sandbox run publishes events to EventBus."""
        from huanxin.event_bus import event_bus
        from huanxin.sandbox import SandboxManager

        client.app.extra["sandbox_manager"] = SandboxManager()

        with event_bus._lock:
            event_bus._queues.clear()

        q, sub_id = event_bus.subscribe()

        try:
            resp = client.post("/api/dashboard/sandbox/run", json={
                "code": "print('bus test')",
                "engine": "local_direct",
            })
            assert resp.status_code == 200

            import json
            data = json.loads(q.get(timeout=2))
            assert data["type"] == "sandbox"
        finally:
            event_bus.unsubscribe(sub_id)


# ── Event Types Registry ──────────────────────────────────────────

class TestEventTypes:
    def test_all_types_defined(self):
        from huanxin.event_publisher import EVENT_TYPES
        assert "dispatch" in EVENT_TYPES
        assert "pipeline" in EVENT_TYPES
        assert "sandbox" in EVENT_TYPES
        assert "governance" in EVENT_TYPES
        assert "healing" in EVENT_TYPES
        assert "approval" in EVENT_TYPES
        assert "memory" in EVENT_TYPES
        assert "eval" in EVENT_TYPES
        assert "alert" in EVENT_TYPES
        assert len(EVENT_TYPES) == 9
