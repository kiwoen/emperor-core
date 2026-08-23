"""Tests for built-in alert rules — ensure_builtin_rules, fire_rule,
and Scheduler._tick integration.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from jarvis.alerts import (
    AlertManager,
    Alert,
    _failure_spike_condition,
    _evolution_stagnation_condition,
    _format_builtin_message,
)
from jarvis.court.scheduler import Scheduler, SchedulerState


# ══════════════════════════════════════════════════════════════════
# Helpers — build fake emperor/court/task_engine for testing
# ══════════════════════════════════════════════════════════════════


def _fake_outcome(success: bool):
    """Create a mock task outcome."""
    o = MagicMock()
    o.success = success
    return o


def _make_emperor(active_ministers=None, outcomes=None, history_records=None):
    """Build a mock emperor with configurable court / task_engine state."""
    emp = MagicMock()

    # Court
    court = emp.court
    if active_ministers is not None:
        type(court).active_ministers = PropertyMock(
            return_value=list(active_ministers)
            if isinstance(active_ministers, list)
            else [f"m{i}" for i in range(active_ministers)]
        )

    # Task engine
    engine = emp.task_engine
    if outcomes is not None:
        engine._outcomes = outcomes
    else:
        engine._outcomes = []

    # Court history
    if history_records is not None:
        history = court.history
        history._records = history_records

    return emp


# ── Fake CycleRecord ───────────────────────────────────────────────


class FakeCycleRecord:
    """Lightweight stand-in for CycleRecord in tests."""

    def __init__(self, merit_mean: float):
        self.merit_mean = merit_mean


# ══════════════════════════════════════════════════════════════════
# ensure_builtin_rules
# ══════════════════════════════════════════════════════════════════


class TestEnsureBuiltinRules:
    def test_registers_three_rules(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=5)
        mgr.ensure_builtin_rules(emp)

        names = mgr.list_builtin_rules()
        assert len(names) == 3
        assert "minister_depletion" in names
        assert "task_failure_spike" in names
        assert "evolution_stagnation" in names

    def test_idempotent(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=5)

        mgr.ensure_builtin_rules(emp)
        mgr.ensure_builtin_rules(emp)
        mgr.ensure_builtin_rules(emp)

        assert len(mgr.list_builtin_rules()) == 3


# ══════════════════════════════════════════════════════════════════
# minister_depletion rule
# ══════════════════════════════════════════════════════════════════


class TestMinisterDepletion:
    def test_does_not_trigger_when_ministers_sufficient(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=5)
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("minister_depletion", emp)
        assert result is None

    def test_triggers_when_ministers_below_threshold(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=["m1", "m2"])  # 2
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("minister_depletion", emp)
        assert result is not None
        assert result.rule_name == "minister_depletion"
        assert result.severity == "warning"
        assert "2" in result.message

    def test_triggers_at_exactly_threshold(self):
        """Threshold is < 3, so exactly 3 should NOT trigger."""
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=["m1", "m2", "m3"])
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("minister_depletion", emp)
        assert result is None

    def test_cooldown_prevents_repeated_fires(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=["m1"])  # 1 → triggers
        mgr.ensure_builtin_rules(emp)
        result1 = mgr.fire_rule("minister_depletion", emp)
        assert result1 is not None

        # Immediate re-fire should be blocked by cooldown
        result2 = mgr.fire_rule("minister_depletion", emp)
        assert result2 is None


# ══════════════════════════════════════════════════════════════════
# task_failure_spike rule
# ══════════════════════════════════════════════════════════════════


class TestTaskFailureSpike:
    def test_does_not_trigger_when_rate_below_threshold(self):
        mgr = AlertManager()
        outcomes = [
            _fake_outcome(True),
            _fake_outcome(True),
            _fake_outcome(False),  # 1/3 = 33%
        ]
        emp = _make_emperor(active_ministers=3, outcomes=outcomes)
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("task_failure_spike", emp)
        assert result is None

    def test_triggers_when_rate_exceeds_threshold(self):
        mgr = AlertManager()
        outcomes = [
            _fake_outcome(False),
            _fake_outcome(False),
            _fake_outcome(True),  # 2/3 = 66%
        ]
        emp = _make_emperor(active_ministers=3, outcomes=outcomes)
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("task_failure_spike", emp)
        assert result is not None
        assert result.rule_name == "task_failure_spike"
        assert result.severity == "error"

    def test_no_outcomes_no_trigger(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=3, outcomes=[])
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("task_failure_spike", emp)
        assert result is None

    def test_all_failures_triggers(self):
        mgr = AlertManager()
        outcomes = [_fake_outcome(False), _fake_outcome(False)]
        emp = _make_emperor(active_ministers=3, outcomes=outcomes)
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("task_failure_spike", emp)
        assert result is not None


# ══════════════════════════════════════════════════════════════════
# evolution_stagnation rule
# ══════════════════════════════════════════════════════════════════


class TestEvolutionStagnation:
    def test_does_not_trigger_when_improving(self):
        mgr = AlertManager()
        records = [
            FakeCycleRecord(1.0),
            FakeCycleRecord(1.5),  # improved
            FakeCycleRecord(2.0),  # improved
            FakeCycleRecord(2.5),  # improved
        ]
        emp = _make_emperor(active_ministers=3, history_records=records)
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("evolution_stagnation", emp)
        assert result is None

    def test_triggers_when_three_consecutive_non_improvements(self):
        mgr = AlertManager()
        records = [
            FakeCycleRecord(3.0),
            FakeCycleRecord(3.0),  # flat
            FakeCycleRecord(2.5),  # declined
            FakeCycleRecord(2.0),  # declined
        ]
        emp = _make_emperor(active_ministers=3, history_records=records)
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("evolution_stagnation", emp)
        assert result is not None
        assert result.rule_name == "evolution_stagnation"
        assert result.severity == "warning"

    def test_not_enough_records_no_trigger(self):
        mgr = AlertManager()
        records = [
            FakeCycleRecord(1.0),
            FakeCycleRecord(1.0),
            FakeCycleRecord(1.0),  # only 3 records, need 4
        ]
        emp = _make_emperor(active_ministers=3, history_records=records)
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("evolution_stagnation", emp)
        assert result is None

    def test_intermittent_improvement_resets(self):
        """If one of the last 3 transitions improved, don't trigger."""
        mgr = AlertManager()
        records = [
            FakeCycleRecord(1.0),
            FakeCycleRecord(0.8),  # declined
            FakeCycleRecord(0.7),  # declined
            FakeCycleRecord(0.9),  # improved!
        ]
        emp = _make_emperor(active_ministers=3, history_records=records)
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("evolution_stagnation", emp)
        assert result is None


# ══════════════════════════════════════════════════════════════════
# fire_rule edge cases
# ══════════════════════════════════════════════════════════════════


class TestFireRuleEdgeCases:
    def test_unknown_rule_returns_none(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=5)
        result = mgr.fire_rule("nonexistent", emp)
        assert result is None

    def test_disabled_rule_skipped(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=["m1"])
        mgr.ensure_builtin_rules(emp)
        # Disable the rule
        mgr._builtin_rules["minister_depletion"]["enabled"] = False
        result = mgr.fire_rule("minister_depletion", emp)
        assert result is None


# ══════════════════════════════════════════════════════════════════
# Scheduler._tick integration
# ══════════════════════════════════════════════════════════════════


class TestSchedulerTickIntegration:
    def test_tick_evaluates_builtin_rules(self):
        """Scheduler._tick should call fire_rule for each built-in rule."""
        emp = MagicMock()
        emp.court.active_ministers = ["m1", "m2", "m3", "m4", "m5"]
        emp.court.history._records = []
        emp.task_engine._outcomes = []

        mgr = AlertManager()
        mgr.ensure_builtin_rules(emp)

        sched = Scheduler(emp)
        sched._state = SchedulerState.RUNNING
        sched._alert_manager = mgr

        # Spy on fire_rule
        with patch.object(mgr, "fire_rule", wraps=mgr.fire_rule) as spy:
            sched._tick()

        # Should have called fire_rule for each of the 3 built-in rules
        assert spy.call_count == 3
        called_rules = {call.args[0] for call in spy.call_args_list}
        assert called_rules == {"minister_depletion", "task_failure_spike", "evolution_stagnation"}

    def test_tick_skips_builtin_when_no_alert_manager(self):
        emp = MagicMock()
        sched = Scheduler(emp)
        sched._state = SchedulerState.RUNNING
        sched._alert_manager = None  # explicitly None

        # Should not raise
        sched._tick()


# ══════════════════════════════════════════════════════════════════
# Module-level condition helpers (unit)
# ══════════════════════════════════════════════════════════════════


class TestConditionHelpers:
    def test_failure_spike_empty_outcomes(self):
        emp = _make_emperor(outcomes=[])
        assert _failure_spike_condition(emp) is False

    def test_failure_spike_below_threshold(self):
        outcomes = [_fake_outcome(True), _fake_outcome(False)]
        emp = _make_emperor(outcomes=outcomes)
        assert _failure_spike_condition(emp) is False

    def test_failure_spike_above_threshold(self):
        outcomes = [_fake_outcome(False), _fake_outcome(False), _fake_outcome(True)]
        emp = _make_emperor(outcomes=outcomes)
        assert _failure_spike_condition(emp) is True

    def test_evolution_stagnation_improving(self):
        records = [
            FakeCycleRecord(1.0),
            FakeCycleRecord(1.5),
            FakeCycleRecord(2.0),
            FakeCycleRecord(2.5),
        ]
        emp = _make_emperor(history_records=records)
        assert _evolution_stagnation_condition(emp) is False

    def test_evolution_stagnation_stagnated(self):
        records = [
            FakeCycleRecord(3.0),
            FakeCycleRecord(3.0),
            FakeCycleRecord(2.5),
            FakeCycleRecord(2.0),
        ]
        emp = _make_emperor(history_records=records)
        assert _evolution_stagnation_condition(emp) is True

    def test_format_builtin_message_active(self):
        emp = _make_emperor(active_ministers=["a", "b"])
        result = _format_builtin_message(
            "Active ministers dropped below 3 (current: {active})",
            "minister_depletion",
            emp,
        )
        assert "2" in result
        assert "{active}" not in result

    def test_format_builtin_message_rate(self):
        outcomes = [_fake_outcome(False), _fake_outcome(True)]
        emp = _make_emperor(outcomes=outcomes)
        result = _format_builtin_message(
            "Task failure rate is {rate:.0%} (>50% threshold)",
            "task_failure_spike",
            emp,
        )
        assert "50%" in result


# ══════════════════════════════════════════════════════════════════
# history integration
# ══════════════════════════════════════════════════════════════════


class TestAlertHistory:
    def test_fired_alerts_appear_in_history(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=["m1"])
        mgr.ensure_builtin_rules(emp)
        mgr.fire_rule("minister_depletion", emp)

        hist = mgr.history()
        assert len(hist) == 1
        assert hist[0].rule_name == "minister_depletion"

    def test_clear_history(self):
        mgr = AlertManager()
        emp = _make_emperor(active_ministers=["m1"])
        mgr.ensure_builtin_rules(emp)
        mgr.fire_rule("minister_depletion", emp)
        mgr.clear_history()
        assert len(mgr.history()) == 0


# ══════════════════════════════════════════════════════════════════
# DB persistence on alert trigger
# ══════════════════════════════════════════════════════════════════


class TestAlertDBPersistence:
    """Tests that fired alerts are persisted to the database."""

    def test_fire_rule_persists_to_db(self, tmp_path):
        """fire_rule() with db set writes to alert_history."""
        from jarvis.database import Database

        db_path = str(tmp_path / "test_alert.db")
        db = Database(db_path)

        mgr = AlertManager(db=db)
        emp = _make_emperor(active_ministers=["m1"])  # 1 minister → triggers depletion
        mgr.ensure_builtin_rules(emp)

        result = mgr.fire_rule("minister_depletion", emp)
        assert result is not None

        history = db.get_alert_history(limit=10)
        assert len(history) >= 1, "Alert history DB should not be empty"
        row = history[0]
        assert row["rule_name"] == "minister_depletion"
        assert row["level"] == "warning"
        assert len(row["message"]) > 0

    def test_evaluate_persists_to_db(self, tmp_path):
        """evaluate() with db set writes threshold-based alerts to alert_history."""
        from jarvis.database import Database
        from jarvis.alerts import AlertRule

        db_path = str(tmp_path / "test_alert_eval.db")
        db = Database(db_path)

        mgr = AlertManager(db=db)
        mgr.add_rule(AlertRule(
            name="test_low_metric",
            metric="test_metric",
            threshold=0.5,
            operator="lt",
            severity="warning",
            message="Test metric is low",
            cooldown_seconds=0.0,
        ))

        mgr.evaluate({"test_metric": 0.1})
        history = db.get_alert_history(limit=10)
        assert len(history) >= 1
        assert history[0]["rule_name"] == "test_low_metric"

    def test_alert_no_db_no_error(self):
        """fire_rule() without db should not crash."""
        mgr = AlertManager(db=None)
        emp = _make_emperor(active_ministers=["m1"])
        mgr.ensure_builtin_rules(emp)
        result = mgr.fire_rule("minister_depletion", emp)
        assert result is not None
