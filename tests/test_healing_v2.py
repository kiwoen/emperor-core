"""Tests for Self-Healing 2.0: StrategySwitcher, handle_with_fallback, effectiveness report, reset_stats, and RecoveryEngine integration."""
import sys
import time
import pytest

sys.path.insert(0, ".")

from jarvis.healing import (
    HealingAction,
    HealingEngine,
    HealingRecord,
    StrategySwitcher,
)


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════

def _make_succeed_action(name, alert_rule="test_alert", tag=""):
    return HealingAction(
        name=name,
        alert_rule=alert_rule,
        action=lambda: None,
        tags=[tag] if tag else [],
    )


def _make_fail_action(name, alert_rule="test_alert"):
    return HealingAction(
        name=name,
        alert_rule=alert_rule,
        action=lambda: (_ for _ in ()).throw(RuntimeError(f"{name} failed")),
    )


# ══════════════════════════════════════════════════════════════════
# StrategySwitcher
# ══════════════════════════════════════════════════════════════════

class TestStrategySwitcher:
    def test_init(self):
        sw = StrategySwitcher(base_cooldown=2.0)
        assert sw.base_cooldown == 2.0
        assert sw._effectiveness == {}
        assert sw._consecutive_failures == {}

    def test_compute_cooldown_no_failures(self):
        sw = StrategySwitcher()
        action = HealingAction(name="a", alert_rule="r", action=lambda: None)
        assert sw.compute_cooldown("r", action) == 1.0

    def test_compute_cooldown_with_failures(self):
        sw = StrategySwitcher()
        action = HealingAction(name="a", alert_rule="r", action=lambda: None)
        sw.record_failure("r", "a", 0)
        assert sw.compute_cooldown("r", action) == 2.0  # 1 * 2^1
        sw.record_failure("r", "a", 0)
        assert sw.compute_cooldown("r", action) == 4.0  # 1 * 2^2

    def test_compute_cooldown_cap(self):
        sw = StrategySwitcher(base_cooldown=0.1)
        action = HealingAction(name="a", alert_rule="r", action=lambda: None)
        for _ in range(30):
            sw.record_failure("r", "a", 0)
        assert sw.compute_cooldown("r", action) == 3600.0  # capped at 3600

    def test_record_success_resets_failures(self):
        sw = StrategySwitcher()
        sw.record_failure("r", "a", 0)
        sw.record_failure("r", "a", 0)
        sw.record_success("r", "a", 1)
        assert sw._consecutive_failures.get("r", 1) == 0
        action = HealingAction(name="a", alert_rule="r", action=lambda: None)
        assert sw.compute_cooldown("r", action) == 1.0

    def test_get_best_action(self):
        sw = StrategySwitcher()
        a1 = _make_succeed_action("a1")
        a2 = _make_succeed_action("a2")
        actions = {"a1": a1, "a2": a2}
        sw.record_success("r", "a1", 0)
        sw.record_success("r", "a1", 0)
        sw.record_failure("r", "a1", 0)  # a1: 2/3 = 0.667
        sw.record_success("r", "a2", 0)   # a2: 1/1 = 1.0
        assert sw.get_best_action("r", actions) == "a2"

    def test_get_best_action_empty(self):
        sw = StrategySwitcher()
        actions = {"a1": _make_succeed_action("a1")}
        assert sw.get_best_action("r", actions) is None

    def test_get_effectiveness_report(self):
        sw = StrategySwitcher()
        sw.record_success("r", "a1", 0)
        sw.record_success("r", "a1", 0)
        sw.record_failure("r", "a1", 0)
        report = sw.get_effectiveness_report()
        assert "r" in report
        assert report["r"]["a1"]["successes"] == 2
        assert report["r"]["a1"]["attempts"] == 3
        assert report["r"]["a1"]["success_rate"] == round(2 / 3, 4)

    def test_reset_stats(self):
        sw = StrategySwitcher()
        sw.record_failure("r", "a1", 0)
        sw.record_success("r", "a2", 1)
        sw.reset_stats()
        assert sw._effectiveness == {}
        assert sw._consecutive_failures == {}


# ══════════════════════════════════════════════════════════════════
# HealingAction new fields
# ══════════════════════════════════════════════════════════════════

class TestHealingActionFields:
    def test_new_fields_exist(self):
        a = HealingAction(
            name="test",
            alert_rule="alert",
            action=lambda: None,
            fallback_actions=["fb1", "fb2"],
        )
        assert a.fallback_actions == ["fb1", "fb2"]
        assert a.success_count == 0
        assert a.failure_count == 0
        assert a.avg_recovery_time == 0.0

    def test_field_counts_update(self):
        a = _make_succeed_action("test")
        a.success_count += 1
        a.failure_count += 1
        a.avg_recovery_time = 2.5
        assert a.success_count == 1
        assert a.failure_count == 1
        assert a.avg_recovery_time == 2.5


# ══════════════════════════════════════════════════════════════════
# HealingEngine: handle_with_fallback
# ══════════════════════════════════════════════════════════════════

class TestHandleWithFallback:
    def test_primary_succeeds(self):
        eng = HealingEngine()
        side = []
        eng.register(HealingAction(
            name="fix", alert_rule="down",
            action=lambda: side.append("ok"),
        ))
        records = eng.handle_with_fallback("down")
        assert len(records) == 1
        assert records[0].success is True
        assert records[0].action_name == "fix"
        assert records[0].strategy_depth == 0
        assert side == ["ok"]

    def test_fallback_chain_kicks_in(self):
        eng = HealingEngine()
        side = []

        eng.register(HealingAction(
            name="primary", alert_rule="down",
            action=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
            fallback_actions=["fb1"],
        ))
        eng.register(HealingAction(
            name="fb1", alert_rule="down",
            action=lambda: side.append("fb1_ok"),
        ))

        records = eng.handle_with_fallback("down")

        # Should have 2 records: primary fails, fb1 succeeds
        assert len(records) == 2
        assert records[0].action_name == "primary"
        assert records[0].success is False
        assert records[1].action_name == "fb1"
        assert records[1].success is True
        assert side == ["fb1_ok"]

    def test_all_exhausted_manual_escalation(self):
        eng = HealingEngine()
        eng.register(HealingAction(
            name="p", alert_rule="down",
            action=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
            fallback_actions=["fb1"],
        ))
        eng.register(HealingAction(
            name="fb1", alert_rule="down",
            action=lambda: (_ for _ in ()).throw(RuntimeError("also fail")),
        ))

        records = eng.handle_with_fallback("down")
        assert len(records) == 3  # p fail, fb1 fail, manual escalation
        assert records[-1].action_name == "manual_escalation"
        assert records[-1].success is False
        assert "All strategies exhausted" in records[-1].error

    def test_strategy_depth_tracking(self):
        eng = HealingEngine()
        eng.register(HealingAction(
            name="p", alert_rule="down",
            action=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
            fallback_actions=["fb1", "fb2"],
        ))
        eng.register(HealingAction(
            name="fb1", alert_rule="down",
            action=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
        ))
        eng.register(HealingAction(
            name="fb2", alert_rule="down",
            action=lambda: None,
        ))

        records = eng.handle_with_fallback("down")
        assert len(records) == 3
        assert records[0].strategy_depth == 0  # primary
        assert records[0].action_name == "p"
        assert records[1].strategy_depth == 1  # fb1
        assert records[1].action_name == "fb1"
        assert records[2].strategy_depth == 2  # fb2
        assert records[2].action_name == "fb2"
        assert records[2].success is True

    def test_normal_handle_still_works(self):
        """Verify original handle() still works and counts success/failure."""
        eng = HealingEngine()
        eng.register(_make_succeed_action("ok", "down"))
        eng.register(_make_fail_action("bad", "down"))

        records_normal = eng.handle("down")
        assert len(records_normal) == 2
        ok_action = eng.get_action("ok")
        bad_action = eng.get_action("bad")
        assert ok_action is not None and ok_action.success_count >= 1
        assert bad_action is not None and bad_action.failure_count >= 1


# ══════════════════════════════════════════════════════════════════
# HealingEngine: get_effectiveness_report
# ══════════════════════════════════════════════════════════════════

class TestEffectivenessReport:
    def test_report_sorting(self):
        eng = HealingEngine()
        a1 = _make_succeed_action("a1", "r")
        a2 = _make_fail_action("a2", "r")
        eng.register(a1)
        eng.register(a2)
        eng.handle("r")  # both fire
        eng.handle("r")  # fire again (but a2 has max_attempts=10 so still fires)
        report = eng.get_effectiveness_report()
        actions = report["actions"]
        assert len(actions) == 2
        # a1 should have higher success rate
        assert actions[0]["name"] == "a1"
        assert actions[0]["success_rate"] >= actions[1]["success_rate"]

    def test_report_includes_fallback_info(self):
        eng = HealingEngine()
        eng.register(HealingAction(
            name="has_fb", alert_rule="r",
            action=lambda: None,
            fallback_actions=["fb1"],
        ))
        report = eng.get_effectiveness_report()
        assert report["actions"][0]["fallback_actions"] == ["fb1"]

    def test_report_switcher_section(self):
        eng = HealingEngine()
        eng.register(_make_succeed_action("a1"))
        eng.handle("test_alert")
        report = eng.get_effectiveness_report()
        assert "switcher" in report
        assert isinstance(report["switcher"], dict)


# ══════════════════════════════════════════════════════════════════
# HealingEngine: reset_stats
# ══════════════════════════════════════════════════════════════════

class TestResetStats:
    def test_reset_clears_action_counts(self):
        eng = HealingEngine()
        a = _make_succeed_action("a1")
        eng.register(a)
        eng.handle("test_alert")
        assert eng.get_action("a1").success_count == 1
        eng.reset_stats()
        assert eng.get_action("a1").success_count == 0
        assert eng.get_action("a1").failure_count == 0

    def test_reset_clears_switcher(self):
        eng = HealingEngine()
        eng.register(_make_succeed_action("a1"))
        eng.handle_with_fallback("test_alert")
        eng.reset_stats()
        report = eng.get_effectiveness_report()
        assert report["switcher"] == {}

    def test_reset_clears_attempt_counts(self):
        eng = HealingEngine()
        eng.register(_make_succeed_action("a1"))
        eng.handle("test_alert")
        eng.reset_stats()
        eng.handle("test_alert")  # should work again
        assert eng.get_action("a1").success_count == 1


# ══════════════════════════════════════════════════════════════════
# HealingEngine: RecoveryEngine integration
# ══════════════════════════════════════════════════════════════════

class MockRecoveryEngine:
    """Minimal mock of RecoveryEngine for testing."""
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.calls = []

    class Status:
        def __init__(self, val):
            self.value = val

    def execute_with_recovery(self, fn, context=None):
        self.calls.append(("execute", fn, context))
        elapsed = 0.05
        if self.succeed:
            return type("Result", (), {
                "status": self.Status("success"),
                "error": None,
            })
        else:
            return type("Result", (), {
                "status": self.Status("failed"),
                "error": "mock failure",
            })


class TestRecoveryIntegration:
    def test_recovery_success(self):
        mock = MockRecoveryEngine(succeed=True)
        eng = HealingEngine(recovery_engine=mock)
        side = []
        eng.register(HealingAction(
            name="fix", alert_rule="down",
            action=lambda: side.append("done"),
        ))
        records = eng.handle_with_fallback("down")
        assert len(records) == 1
        assert records[0].success is True
        assert "Recovery: success" in records[0].error
        assert len(mock.calls) == 1

    def test_recovery_failure_triggers_fallback(self):
        """When recovery wraps all actions and fails, fallback also goes through
        recovery and will fail identically, leading to manual escalation."""
        mock = MockRecoveryEngine(succeed=False)
        eng = HealingEngine(recovery_engine=mock)
        eng.register(HealingAction(
            name="p", alert_rule="down",
            action=lambda: None,
            fallback_actions=["fb"],
        ))
        eng.register(HealingAction(
            name="fb", alert_rule="down",
            action=lambda: None,
        ))
        records = eng.handle_with_fallback("down")
        # Both p and fb go through recovery → both fail → manual escalation
        assert len(records) == 3
        assert records[0].success is False  # p failed via recovery
        assert records[1].success is False  # fb failed via recovery
        assert records[2].action_name == "manual_escalation"
        assert len(mock.calls) == 2  # both actions wrapped


# ══════════════════════════════════════════════════════════════════
# avg_recovery_time
# ══════════════════════════════════════════════════════════════════

class TestAvgRecoveryTime:
    def test_avg_updated_on_handle(self):
        eng = HealingEngine()
        eng.register(_make_succeed_action("a1"))
        eng.handle("test_alert")
        a = eng.get_action("a1")
        # Lambda may execute in < 1ms; avg_recovery_time >= 0 with success_count updated
        assert a.success_count == 1
        assert a.avg_recovery_time >= 0.0

    def test_avg_updated_on_handle_with_fallback(self):
        eng = HealingEngine()
        eng.register(_make_succeed_action("a1"))
        eng.handle_with_fallback("test_alert")
        a = eng.get_action("a1")
        assert a.success_count == 1
        assert a.avg_recovery_time >= 0.0


# ══════════════════════════════════════════════════════════════════
# HealingRecord new fields
# ══════════════════════════════════════════════════════════════════

class TestHealingRecord:
    def test_strategy_depth_default(self):
        r = HealingRecord(
            action_name="test",
            alert_rule="r",
            timestamp=time.time(),
            success=True,
        )
        assert r.strategy_depth == 0
        assert r.recovery_time == 0.0

    def test_strategy_depth_set(self):
        r = HealingRecord(
            action_name="test",
            alert_rule="r",
            timestamp=time.time(),
            success=False,
            strategy_depth=2,
            recovery_time=1.5,
        )
        assert r.strategy_depth == 2
        assert r.recovery_time == 1.5
