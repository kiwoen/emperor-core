"""Tests for jarvis.alerts — health monitoring and rule evaluation."""

import time
import pytest
from jarvis.alerts import (
    AlertManager, AlertRule, AlertSeverity,
    Alert,
)


# ══════════════════════════════════════════════════════════════════
# AlertRule
# ══════════════════════════════════════════════════════════════════


class TestAlertRule:
    """AlertRule dataclass tests."""

    def test_defaults(self):
        r = AlertRule(name="test", metric="cpu", threshold=80.0)
        assert r.operator == "lt"
        assert r.severity == "warning"
        assert r.message == ""
        assert r.cooldown_seconds == 60.0
        assert r.enabled is True

    def test_custom_severity(self):
        r = AlertRule("crit", "mem", 90, "gt", "critical", "OOM!")
        assert r.severity == "critical"
        assert r.message == "OOM!"

    def test_tags_default(self):
        r = AlertRule(name="x", metric="y", threshold=0)
        assert r.tags == []


# ══════════════════════════════════════════════════════════════════
# AlertManager rule management
# ══════════════════════════════════════════════════════════════════


class TestAlertManagerRules:
    """CRUD on alert rules."""

    def test_add_and_get_rule(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("r1", "success_rate", 0.5, "lt", "critical"))
        r = mgr.get_rule("r1")
        assert r is not None
        assert r.name == "r1"
        assert r.metric == "success_rate"

    def test_get_nonexistent_returns_none(self):
        mgr = AlertManager()
        assert mgr.get_rule("ghost") is None

    def test_remove_rule(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("r1", "m", 1))
        assert mgr.remove_rule("r1") is True
        assert mgr.get_rule("r1") is None

    def test_remove_nonexistent(self):
        mgr = AlertManager()
        assert mgr.remove_rule("ghost") is False

    def test_get_rule_is_copy(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("r1", "m", 1, message="orig"))
        r = mgr.get_rule("r1")
        r.message = "mutated"
        r2 = mgr.get_rule("r1")
        assert r2 is not None
        assert r2.message == "orig"

    def test_list_rules(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("a", "x", 1))
        mgr.add_rule(AlertRule("b", "y", 2))
        rules = mgr.list_rules()
        assert len(rules) == 2

    def test_add_rule_overwrites_by_name(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("dup", "metric_a", 1))
        mgr.add_rule(AlertRule("dup", "metric_b", 99, "gt", "info"))
        r = mgr.get_rule("dup")
        assert r is not None
        assert r.metric == "metric_b"
        assert r.threshold == 99
        assert len(mgr.list_rules()) == 1


# ══════════════════════════════════════════════════════════════════
# Evaluate: basic threshold checks
# ══════════════════════════════════════════════════════════════════


class TestEvaluateBasics:
    """AlertManager.evaluate fires on threshold breaches."""

    def test_fires_on_lt(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("low_success", "success_rate", 0.5, "lt",
                               "critical", "Rate too low"))
        fired = mgr.evaluate({"success_rate": 0.3})
        assert len(fired) == 1
        assert fired[0].rule_name == "low_success"
        assert fired[0].severity == "critical"

    def test_no_fire_when_above_lt(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("low", "rate", 0.5, "lt", "warning"))
        fired = mgr.evaluate({"rate": 0.8})
        assert fired == []

    def test_fires_on_gt(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("high_fail", "failures", 10, "gt"))
        fired = mgr.evaluate({"failures": 15})
        assert len(fired) == 1

    def test_no_fire_when_below_gt(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("high", "count", 100, "gt"))
        fired = mgr.evaluate({"count": 50})
        assert fired == []

    def test_fires_on_eq(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("stopped", "running", 0, "eq",
                               "critical", "Scheduler stopped"))
        fired = mgr.evaluate({"running": 0})
        assert len(fired) == 1

    def test_no_fire_when_ne_eq(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("stopped", "running", 0, "eq"))
        fired = mgr.evaluate({"running": 1})
        assert fired == []

    def test_fires_on_gte(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("warm", "temp", 80, "gte"))
        fired = mgr.evaluate({"temp": 80})
        assert len(fired) == 1

    def test_fires_on_gte_above(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("warm", "temp", 80, "gte"))
        fired = mgr.evaluate({"temp": 90})
        assert len(fired) == 1

    def test_fires_on_lte(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("cold", "temp", 10, "lte"))
        fired = mgr.evaluate({"temp": 10})
        assert len(fired) == 1

    def test_fires_on_lte_below(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("cold", "temp", 10, "lte"))
        fired = mgr.evaluate({"temp": 5})
        assert len(fired) == 1


# ══════════════════════════════════════════════════════════════════
# Evaluate: metric missing, cooldown, disabled
# ══════════════════════════════════════════════════════════════════


class TestEvaluateEdgeCases:
    """Cooldown, missing metrics, disabled rules."""

    def test_missing_metric_skipped(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("r1", "missing_key", 0.5, "lt"))
        fired = mgr.evaluate({"other": 0.3})
        assert fired == []

    def test_disabled_rule_skipped(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("off", "x", 1, enabled=False))
        fired = mgr.evaluate({"x": 0})
        assert fired == []

    def test_cooldown_prevents_refire(self, monkeypatch):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("cooldown_test", "rate", 0.5, "lt",
                               cooldown_seconds=10))

        t0 = 1_000_000.0
        monkeypatch.setattr(time, "time", lambda: t0)
        fired1 = mgr.evaluate({"rate": 0.1})
        assert len(fired1) == 1

        # Same state again at t0+5 — still in cooldown
        monkeypatch.setattr(time, "time", lambda: t0 + 5)
        fired2 = mgr.evaluate({"rate": 0.1})
        assert fired2 == []

        # After cooldown expires
        monkeypatch.setattr(time, "time", lambda: t0 + 11)
        fired3 = mgr.evaluate({"rate": 0.1})
        assert len(fired3) == 1

    def test_cooldown_negative_value_always_fires(self, monkeypatch):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("no_cooldown", "x", 0, "lt",
                               cooldown_seconds=0))
        t0 = 1_000_000.0
        monkeypatch.setattr(time, "time", lambda: t0)
        fired1 = mgr.evaluate({"x": -1})
        assert len(fired1) == 1

        monkeypatch.setattr(time, "time", lambda: t0 + 0.1)
        fired2 = mgr.evaluate({"x": -1})
        assert len(fired2) == 1


# ══════════════════════════════════════════════════════════════════
# Handlers
# ══════════════════════════════════════════════════════════════════


class TestHandlers:
    """Custom handler integration."""

    def test_custom_handler_fires(self):
        mgr = AlertManager()
        alerts_seen = []

        mgr.add_rule(AlertRule("test", "val", 0, "lt"))
        mgr.add_handler(lambda a: alerts_seen.append(a))
        mgr.evaluate({"val": -1})
        assert len(alerts_seen) == 1
        assert alerts_seen[0].rule_name == "test"

    def test_multiple_handlers(self):
        mgr = AlertManager()
        bag = []
        mgr.add_rule(AlertRule("t", "x", 0, "lt"))
        mgr.add_handler(lambda a: bag.append(1))
        mgr.add_handler(lambda a: bag.append(2))
        mgr.evaluate({"x": -1})
        # log handler + 2 custom = 3 total appends
        assert len(bag) == 2

    def test_handler_exception_does_not_crash(self, caplog):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("t", "x", 0, "lt"))

        def bad(_):
            raise RuntimeError("boom")

        mgr.add_handler(bad)
        mgr.evaluate({"x": -1})
        # Should not raise, should log error
        assert "boom" in caplog.text or "Handler failed" in caplog.text


# ══════════════════════════════════════════════════════════════════
# History
# ══════════════════════════════════════════════════════════════════


class TestHistory:
    """Alert firing history."""

    def test_history_lifo(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("a", "v", 0, "lt", cooldown_seconds=0))
        mgr.add_rule(AlertRule("b", "w", 0, "lt", cooldown_seconds=0))
        mgr.evaluate({"v": -1, "w": -1})
        history = mgr.history()
        assert len(history) == 2
        # newest first
        assert history[0].rule_name == "b"
        assert history[1].rule_name == "a"

    def test_history_limit(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("r", "x", 0, "lt", cooldown_seconds=0))
        for _ in range(10):
            mgr.evaluate({"x": -1})
        assert len(mgr.history(limit=3)) == 3

    def test_clear_history(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("r", "x", 0, "lt", cooldown_seconds=0))
        mgr.evaluate({"x": -1})
        mgr.clear_history()
        assert mgr.history() == []

    def test_history_capped_200(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("r", "x", 0, "lt", cooldown_seconds=0))
        for _ in range(250):
            mgr.evaluate({"x": -1})
        h = mgr.history(limit=250)
        assert len(h) <= 200


# ══════════════════════════════════════════════════════════════════
# Alert dataclass
# ══════════════════════════════════════════════════════════════════


class TestAlertDataclass:
    def test_alert_fields(self):
        a = Alert("r", "warning", "msg", "metric", 0.3, 0.5, "lt", 1000.0)
        assert a.rule_name == "r"
        assert a.severity == "warning"
        assert a.message == "msg"
        assert a.current_value == 0.3
        assert a.threshold == 0.5


# ══════════════════════════════════════════════════════════════════
# Multi-rule evaluation
# ══════════════════════════════════════════════════════════════════


class TestMultiRule:
    """Multiple rules evaluated in one pass."""

    def test_fires_multiple_rules(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("low", "rate", 0.5, "lt", cooldown_seconds=0))
        mgr.add_rule(AlertRule("high", "fail", 10, "gt", cooldown_seconds=0))
        fired = mgr.evaluate({"rate": 0.1, "fail": 20})
        assert len(fired) == 2

    def test_only_matching_fire(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule("low", "rate", 0.5, "lt", cooldown_seconds=0))
        mgr.add_rule(AlertRule("high", "fail", 10, "gt", cooldown_seconds=0))
        fired = mgr.evaluate({"rate": 0.8, "fail": 5})
        assert fired == []
