"""Focused branch tests for bounded autonomy evaluation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from huanxin.bounded_autonomy import (
    ActionSpace,
    ActionZone,
    BoundedAutonomyEngine,
)


def test_default_classification_is_fail_safe():
    engine = BoundedAutonomyEngine()
    assert engine.classify({"tool": "read", "prompt": "list files"}) is ActionZone.GREEN
    assert engine.classify({"tool": "write", "prompt": "update record"}, {"domain": "database"}) is ActionZone.YELLOW
    assert engine.classify({"tool": "delete", "prompt": "delete files"}) is ActionZone.RED
    assert engine.classify({"tool": "unknown"}) is ActionZone.YELLOW


def test_action_space_requires_all_configured_conditions():
    space = ActionSpace(
        name="restricted",
        zone=ActionZone.RED,
        domains={"security"},
        risk_levels={"high"},
        capabilities={"delete"},
        keywords={"secret"},
    )
    action = {"tool": "delete", "prompt": "remove secret"}
    assert space.matches(action, {"domain": "security", "risk_level": "high"})
    assert not space.matches(action, {"domain": "general", "risk_level": "high"})
    assert not space.matches(action, {"domain": "security", "risk_level": "low"})
    assert not space.matches({"tool": "read", "prompt": "show secret"}, {"domain": "security", "risk_level": "high"})


def test_custom_matcher_and_space_lifecycle():
    engine = BoundedAutonomyEngine(load_defaults=False)
    space = ActionSpace(
        name="custom",
        zone=ActionZone.GREEN,
        custom_matcher=lambda action, ctx: ctx.get("allow") is True,
    )
    engine.register_space(space)
    assert engine.get_space("custom") is space
    assert engine.classify("anything", {"allow": True}) is ActionZone.GREEN
    assert engine.classify("anything", {"allow": False}) is ActionZone.YELLOW
    assert engine.deregister_space("custom") is True
    assert engine.deregister_space("custom") is False


def test_yellow_without_approval_engine_is_blocked():
    result = BoundedAutonomyEngine().evaluate(
        {"tool": "update", "prompt": "update settings"},
        {"domain": "config"},
        task_id="task-1",
    )
    assert result.zone is ActionZone.YELLOW
    assert result.can_proceed is False
    assert result.needs_approval is True
    assert result.approval_request_id is None
    assert result.task_id == "task-1"


def test_yellow_approval_success_and_failure():
    class Approval:
        def create_request(self, **kwargs):
            assert kwargs["task_id"] == "task-2"
            return SimpleNamespace(id="approval-1")

    ok = BoundedAutonomyEngine(approval_engine=Approval()).evaluate(
        {"tool": "update", "prompt": "update settings"},
        {"domain": "config"},
        task_id="task-2",
    )
    assert ok.approval_request_id == "approval-1"
    assert ok.needs_approval is True
    assert ok.can_proceed is False

    class BrokenApproval:
        def create_request(self, **kwargs):
            raise RuntimeError("approval unavailable")

    failed = BoundedAutonomyEngine(approval_engine=BrokenApproval()).evaluate(
        {"tool": "update", "prompt": "update settings"},
        {"domain": "config"},
    )
    assert failed.can_proceed is False
    assert failed.needs_approval is True
    assert "unavailable" in failed.reason


@pytest.mark.parametrize(
    ("gov_result", "expected_approval", "reason"),
    [
        (SimpleNamespace(passed=True, needs_approval=False, reason="ok", approval_request_id=None), False, "passed"),
        (SimpleNamespace(passed=False, needs_approval=True, reason="review", approval_request_id="a-2"), True, "requires approval"),
        (SimpleNamespace(passed=False, needs_approval=False, reason="blocked", approval_request_id=None), False, "blocked"),
    ],
)
def test_red_governance_outcomes(gov_result, expected_approval, reason):
    class Governance:
        def validate(self, **kwargs):
            return gov_result

    result = BoundedAutonomyEngine(governance_agent=Governance()).evaluate(
        {"tool": "delete", "prompt": "delete files"},
        {"domain": "general"},
        task_id="task-3",
    )
    assert result.zone is ActionZone.RED
    assert result.can_proceed is False
    assert result.needs_approval is expected_approval
    assert reason in result.reason
    if expected_approval:
        assert result.approval_request_id == "a-2"


def test_red_governance_exception_remains_blocked():
    class BrokenGovernance:
        def validate(self, **kwargs):
            raise RuntimeError("governance unavailable")

    result = BoundedAutonomyEngine(governance_agent=BrokenGovernance()).evaluate(
        {"tool": "delete", "prompt": "delete files"}
    )
    assert result.zone is ActionZone.RED
    assert result.can_proceed is False
    assert "validation error" in result.reason


def test_result_serialization_omits_internal_governance_object():
    result = BoundedAutonomyEngine().evaluate({"tool": "read", "prompt": "show status"})
    data = result.to_dict()
    assert data["zone"] == "green"
    assert data["can_proceed"] is True
    assert "governance_result" not in data
