"""Tests for jarvis.healing — HealingEngine unit tests."""

import time
from unittest.mock import MagicMock

import pytest

from jarvis.healing import HealingAction, HealingEngine, HealingRecord


# ══════════════════════════════════════════════════════════════════
# HealingAction
# ══════════════════════════════════════════════════════════════════


class TestHealingAction:
    def test_defaults(self):
        """Default cooldown 300s, max_attempts 10, enabled True."""
        a = HealingAction(
            name="restart",
            alert_rule="scheduler_down",
            action=lambda: None,
        )
        assert a.cooldown_seconds == 300.0
        assert a.max_attempts == 10
        assert a.enabled is True
        assert a.tags == []

    def test_custom_fields(self):
        """All fields customisable."""
        a = HealingAction(
            name="evolve_emergency",
            alert_rule="low_success_rate",
            action=lambda: 42,
            cooldown_seconds=60.0,
            max_attempts=3,
            enabled=False,
            tags=["critical", "auto"],
        )
        assert a.max_attempts == 3
        assert a.enabled is False
        assert a.tags == ["critical", "auto"]


# ══════════════════════════════════════════════════════════════════
# HealingEngine — registration
# ══════════════════════════════════════════════════════════════════


class TestHealingEngineRegistration:
    def test_register_and_get(self):
        """Registered action retrievable by name (copy)."""
        engine = HealingEngine()
        engine.register(HealingAction(
            name="restart", alert_rule="down", action=lambda: None,
        ))
        a = engine.get_action("restart")
        assert a is not None
        assert a.name == "restart"
        assert a.alert_rule == "down"
        assert a.enabled is True
        # Copy, not original
        a.name = "modified"
        assert engine.get_action("restart").name == "restart"

    def test_list_actions(self):
        """list_actions returns copies of all registered actions."""
        engine = HealingEngine()
        for i in range(3):
            engine.register(HealingAction(
                name=f"action_{i}", alert_rule=f"rule_{i}", action=lambda: None,
            ))
        actions = engine.list_actions()
        assert len(actions) == 3
        names = {a.name for a in actions}
        assert names == {"action_0", "action_1", "action_2"}

    def test_unregister(self):
        """Unregister removes action."""
        engine = HealingEngine()
        engine.register(HealingAction(
            name="temp", alert_rule="test", action=lambda: None,
        ))
        assert engine.unregister("temp") is True
        assert engine.get_action("temp") is None

    def test_unregister_nonexistent(self):
        """Unregister nonexistent returns False."""
        engine = HealingEngine()
        assert engine.unregister("ghost") is False


# ══════════════════════════════════════════════════════════════════
# HealingEngine — triggering
# ══════════════════════════════════════════════════════════════════


class TestHealingEngineHandle:
    def test_handle_matches_alert(self):
        """Action triggers when alert rule matches."""
        called = []
        engine = HealingEngine()
        engine.register(HealingAction(
            name="restart", alert_rule="scheduler_down",
            action=lambda: called.append(42),
        ))
        records = engine.handle("scheduler_down")
        assert len(records) == 1
        rec = records[0]
        assert rec.action_name == "restart"
        assert rec.alert_rule == "scheduler_down"
        assert rec.success is True
        assert called == [42]

    def test_handle_no_match(self):
        """No action fires for unregistered alert rule."""
        engine = HealingEngine()
        engine.register(HealingAction(
            name="restart", alert_rule="down", action=lambda: 1,
        ))
        records = engine.handle("unknown_alert")
        assert records == []

    def test_handle_disabled_action(self):
        """Disabled action does not trigger."""
        engine = HealingEngine()
        engine.register(HealingAction(
            name="restart", alert_rule="down", action=lambda: 1,
            enabled=False,
        ))
        records = engine.handle("down")
        assert records == []

    def test_handle_cooldown(self):
        """Action respects cooldown — only fires once within window."""
        called = []
        engine = HealingEngine()
        engine.register(HealingAction(
            name="restart", alert_rule="down",
            action=lambda: called.append(1),
            cooldown_seconds=999.0,  # very long
        ))
        # First call
        rec1 = engine.handle("down")
        assert len(rec1) == 1
        assert called == [1]

        # Second call within cooldown — skip
        rec2 = engine.handle("down")
        assert rec2 == []
        assert called == [1]

    def test_handle_max_attempts(self):
        """Action respects max_attempts limit."""
        engine = HealingEngine()
        engine.register(HealingAction(
            name="restart", alert_rule="down",
            action=lambda: 1,
            max_attempts=2,
            cooldown_seconds=0,
        ))
        rec1 = engine.handle("down")
        rec2 = engine.handle("down")
        rec3 = engine.handle("down")
        assert len(rec1) == 1
        assert len(rec2) == 1
        assert rec3 == []  # exhausted

    def test_handle_action_error(self):
        """Action that raises Exception is recorded as failure."""
        def bad():
            raise RuntimeError("boom")

        engine = HealingEngine()
        engine.register(HealingAction(
            name="faulty", alert_rule="error",
            action=bad,
        ))
        records = engine.handle("error")
        assert len(records) == 1
        rec = records[0]
        assert rec.success is False
        assert "boom" in rec.error

    def test_handle_batch(self):
        """handle_batch processes multiple rules at once."""
        results = []
        engine = HealingEngine()
        engine.register(HealingAction(
            name="action_a", alert_rule="rule_a",
            action=lambda: results.append("a"),
            cooldown_seconds=0,
        ))
        engine.register(HealingAction(
            name="action_b", alert_rule="rule_b",
            action=lambda: results.append("b"),
            cooldown_seconds=0,
        ))
        engine.register(HealingAction(
            name="action_nomatch", alert_rule="rule_c",
            action=lambda: results.append("c"),
            cooldown_seconds=0,
        ))

        records = engine.handle_batch(["rule_a", "rule_b", "unknown"])
        assert len(records) == 2
        assert results == ["a", "b"]

    def test_multiple_actions_same_alert(self):
        """Multiple actions can match the same alert rule."""
        results = []
        engine = HealingEngine()
        engine.register(HealingAction(
            name="email", alert_rule="critical",
            action=lambda: results.append("email"),
            cooldown_seconds=0,
        ))
        engine.register(HealingAction(
            name="restart", alert_rule="critical",
            action=lambda: results.append("restart"),
            cooldown_seconds=0,
        ))
        records = engine.handle("critical")
        assert len(records) == 2
        assert "email" in results
        assert "restart" in results


# ══════════════════════════════════════════════════════════════════
# HealingEngine — history
# ══════════════════════════════════════════════════════════════════


class TestHealingEngineHistory:
    def test_history_records(self):
        """Executed actions appear in history."""
        engine = HealingEngine()
        for i in range(5):
            engine.register(HealingAction(
                name=f"action_{i}", alert_rule=f"rule_{i}",
                action=lambda: 1, cooldown_seconds=0,
            ))
        for i in range(5):
            engine.handle(f"rule_{i}")

        history = engine.history()
        assert len(history) == 5
        # Newest first
        assert history[0].action_name == "action_4"

    def test_history_limit(self):
        """History respects limit parameter."""
        engine = HealingEngine()
        for i in range(10):
            engine.register(HealingAction(
                name=f"a{i}", alert_rule=f"r{i}",
                action=lambda: 1, cooldown_seconds=0,
            ))
        for i in range(10):
            engine.handle(f"r{i}")

        assert len(engine.history(limit=3)) == 3

    def test_history_trimming(self):
        """Old records trimmed when exceeding 200."""
        engine = HealingEngine()
        engine.register(HealingAction(
            name="a0", alert_rule="r0",
            action=lambda: 1, cooldown_seconds=0,
        ))
        # Fill beyond 200
        engine._history = [
            HealingRecord("x", "y", 0, True) for _ in range(250)
        ]
        engine.handle("r0")
        # Should be trimmed to ≤ 100 after handle logics
        assert len(engine._history) <= 200

    def test_clear_history(self):
        """clear_history empties records."""
        engine = HealingEngine()
        engine.register(HealingAction(
            name="a1", alert_rule="r1",
            action=lambda: 1, cooldown_seconds=0,
        ))
        engine.handle("r1")
        assert len(engine.history()) == 1
        engine.clear_history()
        assert engine.history() == []

    def test_reset_attempts_specific(self):
        """reset_attempts on a specific action clears its counters."""
        engine = HealingEngine()
        engine.register(HealingAction(
            name="restart", alert_rule="down",
            action=lambda: 1, max_attempts=1, cooldown_seconds=0,
        ))
        records = engine.handle("down")
        assert len(records) == 1
        # Exhausted
        assert engine.handle("down") == []
        engine.reset_attempts("restart")
        records = engine.handle("down")
        assert len(records) == 1

    def test_reset_attempts_all(self):
        """reset_attempts with no name clears all counters."""
        engine = HealingEngine()
        for i in range(3):
            engine.register(HealingAction(
                name=f"a{i}", alert_rule=f"r{i}",
                action=lambda: 1, max_attempts=1, cooldown_seconds=0,
            ))
        for i in range(3):
            records = engine.handle(f"r{i}")
            assert len(records) == 1
            assert engine.handle(f"r{i}") == []  # exhausted

        engine.reset_attempts()
        for i in range(3):
            assert len(engine.handle(f"r{i}")) == 1


# ══════════════════════════════════════════════════════════════════
# HealingRecord
# ══════════════════════════════════════════════════════════════════


class TestHealingRecord:
    def test_record_fields(self):
        """All fields correctly stored."""
        r = HealingRecord(
            action_name="restart",
            alert_rule="scheduler_down",
            timestamp=1234567890.0,
            success=True,
            error="",
        )
        assert r.action_name == "restart"
        assert r.alert_rule == "scheduler_down"
        assert r.timestamp == 1234567890.0
        assert r.success is True
        assert r.error == ""

    def test_record_error(self):
        """Error field populated on failure."""
        r = HealingRecord(
            action_name="rebalance",
            alert_rule="low_rate",
            timestamp=0.0,
            success=False,
            error="connection refused",
        )
        assert r.success is False
        assert "connection" in r.error
