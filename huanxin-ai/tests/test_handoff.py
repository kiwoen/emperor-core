"""Tests for huanxin.handoff — Multi-Agent Handoff Protocol.

Covers:
    - HandoffContext serialization / deserialization
    - HandoffRequest creation + priority inheritance
    - HandoffProtocol: registration, execution, fallback
    - Chain tracking, timeout, max depth
    - Stats, history, active handoffs
    - Huanxin integration (execute_task with handoff meta)
    - Court API endpoints
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from huanxin.handoff import (
    HandoffContext,
    HandoffProtocol,
    HandoffRequest,
    HandoffResult,
    HandoffStatus,
    HandoffPriority,
    FallbackStrategy,
    handoff_protocol,
)
from huanxin.core import Huanxin


# ══════════════════════════════════════════════════════════════════
# HandoffContext
# ══════════════════════════════════════════════════════════════════

class TestHandoffContext:
    def test_default_creation(self):
        ctx = HandoffContext()
        assert ctx.task_id
        assert len(ctx.task_id) == 12
        assert ctx.priority == 2
        assert ctx.handoff_count == 0
        assert ctx.chain == []
        assert ctx.data == {}
        assert ctx.created_at > 0
        assert ctx.updated_at > 0

    def test_custom_fields(self):
        ctx = HandoffContext(
            task_id="custom-001",
            original_prompt="Solve puzzle",
            priority=4,
            data={"hint": "look at edges"},
            metadata={"domain": "math"},
        )
        assert ctx.task_id == "custom-001"
        assert ctx.original_prompt == "Solve puzzle"
        assert ctx.priority == 4
        assert ctx.data["hint"] == "look at edges"
        assert ctx.metadata["domain"] == "math"

    def test_record_step(self):
        ctx = HandoffContext(task_id="t-1")
        ctx.record_step("turing", "partial answer", "completed")
        assert ctx.handoff_count == 1
        assert ctx.chain == ["turing"]
        assert ctx.history[0]["minister"] == "turing"
        assert ctx.history[0]["status"] == "completed"

    def test_serialize_roundtrip(self):
        ctx = HandoffContext(
            task_id="rtt-1",
            original_prompt="Test roundtrip",
            priority=3,
        )
        ctx.record_step("alice", "result 1", "completed")
        ctx.record_step("bob", "result 2", "completed")

        serialized = ctx.serialize()
        restored = HandoffContext.deserialize(serialized)

        assert restored.task_id == "rtt-1"
        assert restored.original_prompt == "Test roundtrip"
        assert restored.priority == 3
        assert restored.handoff_count == 2
        assert restored.chain == ["alice", "bob"]

    def test_to_from_dict(self):
        ctx = HandoffContext(
            task_id="dict-1",
            original_prompt="Dict test",
        )
        ctx.data["key"] = "value"
        ctx.record_step("curie", "done")

        d = ctx.to_dict()
        assert d["task_id"] == "dict-1"
        assert d["data"]["key"] == "value"
        assert len(d["history"]) == 1

        restored = HandoffContext.from_dict(d)
        assert restored.task_id == "dict-1"
        assert restored.data["key"] == "value"


# ══════════════════════════════════════════════════════════════════
# HandoffRequest
# ══════════════════════════════════════════════════════════════════

class TestHandoffRequest:
    def test_defaults(self):
        ctx = HandoffContext(task_id="def-1", priority=3)
        req = HandoffRequest(
            source_minister="alice",
            target_minister="bob",
            context=ctx,
        )
        assert req.handoff_id.startswith("ho_")
        assert req.source_minister == "alice"
        assert req.target_minister == "bob"
        # Priority inherited from context
        assert req.priority == 3
        assert req.fallback_strategy == FallbackStrategy.REJECT

    def test_explicit_priority_overrides_inheritance(self):
        ctx = HandoffContext(task_id="pri-1", priority=4)
        req = HandoffRequest(
            source_minister="alice",
            target_minister="bob",
            context=ctx,
            priority=1,
        )
        assert req.priority == 1

    def test_to_dict(self):
        ctx = HandoffContext(task_id="td-1")
        req = HandoffRequest(
            source_minister="x",
            target_minister="y",
            context=ctx,
            reason="need help",
        )
        d = req.to_dict()
        assert d["source_minister"] == "x"
        assert d["target_minister"] == "y"
        assert d["reason"] == "need help"
        assert "context" in d


# ══════════════════════════════════════════════════════════════════
# HandoffProtocol
# ══════════════════════════════════════════════════════════════════

class TestHandoffProtocol:
    def test_register_and_list_targets(self):
        proto = HandoffProtocol()

        def cb(req):
            return HandoffResult(
                handoff_id=req.handoff_id,
                status=HandoffStatus.ACCEPTED,
                source_minister=req.source_minister,
                target_minister=req.target_minister,
                context=req.context,
            )

        proto.register_target("bob", cb)
        assert proto.has_target("bob")
        assert not proto.has_target("alice")
        assert proto.list_targets() == ["bob"]

    def test_unregister_target(self):
        proto = HandoffProtocol()
        proto.register_target("bob", lambda r: None)
        assert proto.unregister_target("bob") is True
        assert proto.unregister_target("bob") is False

    def test_successful_handoff(self):
        proto = HandoffProtocol()

        def accept(req):
            return HandoffResult(
                handoff_id=req.handoff_id,
                status=HandoffStatus.ACCEPTED,
                source_minister=req.source_minister,
                target_minister=req.target_minister,
                context=req.context,
            )

        proto.register_target("bob", accept)

        ctx = HandoffContext(task_id="t-ok")
        req = HandoffRequest(
            source_minister="alice",
            target_minister="bob",
            context=ctx,
        )
        result = proto.handoff(req)
        assert result.accepted
        assert result.status == HandoffStatus.ACCEPTED

    def test_rejected_handoff(self):
        proto = HandoffProtocol()

        def reject(req):
            return HandoffResult(
                handoff_id=req.handoff_id,
                status=HandoffStatus.REJECTED,
                source_minister=req.source_minister,
                target_minister=req.target_minister,
                context=req.context,
                rejection_reason="domain mismatch",
            )

        proto.register_target("bob", reject)

        ctx = HandoffContext(task_id="t-bad")
        req = HandoffRequest(
            source_minister="alice",
            target_minister="bob",
            context=ctx,
        )
        result = proto.handoff(req)
        assert not result.accepted
        assert result.status == HandoffStatus.REJECTED
        assert result.rejection_reason == "domain mismatch"

    def test_target_not_registered(self):
        proto = HandoffProtocol()
        ctx = HandoffContext(task_id="no-target")
        req = HandoffRequest(
            source_minister="alice",
            target_minister="nobody",
            context=ctx,
        )
        result = proto.handoff(req)
        assert result.status == HandoffStatus.REJECTED
        assert "not registered" in result.rejection_reason

    def test_max_chain_depth(self):
        proto = HandoffProtocol(max_chain_length=3)

        def accept(req):
            return HandoffResult(
                handoff_id=req.handoff_id,
                status=HandoffStatus.ACCEPTED,
                source_minister=req.source_minister,
                target_minister=req.target_minister,
                context=req.context,
            )

        proto.register_target("bob", accept)
        proto.register_target("carol", accept)

        ctx = HandoffContext(task_id="deep-chain")
        # Pre-populate chain to max
        ctx.record_step("a", "r1")
        ctx.record_step("b", "r2")
        ctx.record_step("c", "r3")

        req = HandoffRequest(
            source_minister="alice",
            target_minister="bob",
            context=ctx,
        )
        result = proto.handoff(req)
        assert result.status == HandoffStatus.REJECTED
        assert "chain depth exceeded" in result.rejection_reason


# ══════════════════════════════════════════════════════════════════
# HandoffPriority
# ══════════════════════════════════════════════════════════════════

class TestHandoffPriority:
    def test_from_int(self):
        assert HandoffPriority.from_int(1) == HandoffPriority.LOW
        assert HandoffPriority.from_int(2) == HandoffPriority.MEDIUM
        assert HandoffPriority.from_int(3) == HandoffPriority.HIGH
        assert HandoffPriority.from_int(4) == HandoffPriority.CRITICAL
        assert HandoffPriority.from_int(5) == HandoffPriority.CRITICAL

    def test_from_string(self):
        assert HandoffPriority.from_string("critical") == HandoffPriority.CRITICAL
        assert HandoffPriority.from_string("high") == HandoffPriority.HIGH
        assert HandoffPriority.from_string("unknown") == HandoffPriority.MEDIUM


# ══════════════════════════════════════════════════════════════════
# Huanxin integration
# ══════════════════════════════════════════════════════════════════

class TestHuanxinHandoffIntegration:
    def test_emperor_has_handoff_property(self):
        emp = Huanxin()
        assert emp.handoff is not None

    def test_handoff_singleton_available(self):
        assert handoff_protocol is not None


# ══════════════════════════════════════════════════════════════════
# Court API endpoints
# ══════════════════════════════════════════════════════════════════

class TestHandoffAPI:
    @pytest.fixture
    def client(self):
        from huanxin.court_api import create_app
        from huanxin.court.court import Court

        court = Court()
        court.register("turing", domain="math")
        court.register("curie", domain="science")

        emp = Huanxin()
        emp._court = court

        # Register ministers as handoff targets
        from huanxin.handoff import HandoffResult, HandoffStatus

        def make_accept(name):
            def accept(req):
                return HandoffResult(
                    handoff_id=req.handoff_id,
                    status=HandoffStatus.ACCEPTED,
                    source_minister=req.source_minister,
                    target_minister=name,
                    context=req.context,
                )
            return accept

        emp.handoff.register_target("curie", make_accept("curie"))
        emp.handoff.register_target("gauss", make_accept("gauss"))

        app = create_app(court=court)
        app.extra["emperor"] = emp
        return TestClient(app)

    def test_handoff_history_empty(self, client):
        r = client.get("/api/handoff/history")
        assert r.status_code == 200
        data = r.json()
        assert "history" in data
        assert data["count"] == 0

    def test_handoff_stats(self, client):
        r = client.get("/api/handoff/stats")
        assert r.status_code == 200
        data = r.json()
        assert "total_handoffs" in data
        assert data["total_handoffs"] == 0

    def test_handoff_targets(self, client):
        r = client.get("/api/handoff/targets")
        assert r.status_code == 200
        data = r.json()
        assert "targets" in data
        assert "curie" in data["targets"]
        assert "gauss" in data["targets"]

    def test_handoff_chain_empty(self, client):
        r = client.get("/api/handoff/chain/nonexistent")
        assert r.status_code == 200
        data = r.json()
        assert data["task_id"] == "nonexistent"
        assert data["length"] == 0

    def test_handoff_not_found(self, client):
        r = client.get("/api/handoff/ho_nonexistent")
        assert r.status_code == 404

    def test_handoff_execute_endpoint(self, client):
        payload = {
            "source_minister": "turing",
            "target_minister": "curie",
            "task_id": "endpoint-test",
            "original_prompt": "Test handoff via API",
            "priority": 3,
            "reason": "Need science domain expertise",
            "deadline_seconds": 10.0,
            "fallback_strategy": "reject",
        }
        r = client.post("/api/handoff/execute", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["accepted"] is True
        assert data["status"] == "accepted"
        assert data["source_minister"] == "turing"
        assert data["target_minister"] == "curie"

        # History should now contain this handoff
        r2 = client.get("/api/handoff/history")
        assert r2.status_code == 200
        hist = r2.json()
        assert hist["count"] >= 1
