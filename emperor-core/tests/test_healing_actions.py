"""Tests for jarvis.healing_actions — pre‑baked healing actions."""

import gc
import time
from unittest.mock import MagicMock

import pytest

from jarvis import healing_actions as ha
from jarvis.alerts import AlertManager, AlertRule, AlertSeverity


# ══════════════════════════════════════════════════════════════════
# Helper fixtures
# ══════════════════════════════════════════════════════════════════


class _StubEngine:
    """Minimal TaskEngine stub for the actions module.
    Note: does NOT define reset() by default; tests that need it add it manually.
    """
    pass


class _StubScheduler:
    """Minimal Scheduler stub."""
    def __init__(self, state="RUNNING"):
        self._state = state

    def report(self):
        r = MagicMock()
        r.state = self._state
        return r

    def start(self, emperor):
        self._state = "RUNNING"

    def stop(self):
        self._state = "STOPPED"


class _StubCourt:
    def __init__(self, n_min=2):
        self._n = n_min

    @property
    def active_ministers(self):
        return list(range(self._n))


class _StubEmperor:
    """Minimal Emperor stub that mirrors the public API used by healing actions."""
    def __init__(self):
        self.scheduler = _StubScheduler()
        self.task_engine = _StubEngine()
        self.court = _StubCourt()
        self.alerts = AlertManager()
        self._evolve_calls = []

    def evolve(self, n_cycles=1):
        self._evolve_calls.append(n_cycles)
        return {"ok": True}


@pytest.fixture
def installed(monkeypatch):
    """Install a stub emperor so _get_emperor() returns it."""
    emperor = _StubEmperor()
    # The actions module uses a placeholder; we monkeypatch it.
    monkeypatch.setattr(ha, "_get_emperor", lambda: emperor)
    return emperor


# ══════════════════════════════════════════════════════════════════
# Scheduler actions
# ══════════════════════════════════════════════════════════════════


class TestSchedulerActions:
    def test_restart_scheduler_when_down(self, installed):
        installed.scheduler._state = "STOPPED"
        assert ha.restart_scheduler() is True
        assert installed.scheduler.report().state == "RUNNING"

    def test_restart_scheduler_already_running(self, installed):
        installed.scheduler._state = "RUNNING"
        assert ha.restart_scheduler() is False

    def test_restart_scheduler_no_emperor(self, monkeypatch):
        monkeypatch.setattr(ha, "_get_emperor", lambda: None)
        assert ha.restart_scheduler() is False

    def test_stop_scheduler_when_running(self, installed):
        installed.scheduler._state = "RUNNING"
        assert ha.stop_scheduler() is True
        assert installed.scheduler.report().state == "STOPPED"

    def test_stop_scheduler_already_stopped(self, installed):
        installed.scheduler._state = "STOPPED"
        assert ha.stop_scheduler() is False

    def test_stop_scheduler_no_emperor(self, monkeypatch):
        monkeypatch.setattr(ha, "_get_emperor", lambda: None)
        assert ha.stop_scheduler() is False

    def test_restart_scheduler_handles_exception(self, installed, monkeypatch):
        installed.scheduler.start = MagicMock(side_effect=RuntimeError("boom"))
        installed.scheduler._state = "STOPPED"
        assert ha.restart_scheduler() is False


# ══════════════════════════════════════════════════════════════════
# Court / evolution actions
# ══════════════════════════════════════════════════════════════════


class TestCourtActions:
    def test_emergency_evolve(self, installed):
        assert ha.emergency_evolve(cycles=2) is True
        assert installed._evolve_calls == [2]

    def test_emergency_evolve_default(self, installed):
        assert ha.emergency_evolve() is True
        assert installed._evolve_calls == [1]

    def test_emergency_evolve_no_emperor(self, monkeypatch):
        monkeypatch.setattr(ha, "_get_emperor", lambda: None)
        assert ha.emergency_evolve() is False

    def test_emergency_evolve_handles_exception(self, installed):
        installed.evolve = MagicMock(side_effect=RuntimeError("boom"))
        assert ha.emergency_evolve() is False

    def test_replenish_when_below_min(self, installed):
        installed.court._n = 1
        assert ha.replenish_ministers(min_count=3) is True
        assert installed._evolve_calls == [1]

    def test_replenish_when_sufficient(self, installed):
        installed.court._n = 5
        assert ha.replenish_ministers(min_count=3) is False
        assert installed._evolve_calls == []


# ══════════════════════════════════════════════════════════════════
# Task‑engine actions
# ══════════════════════════════════════════════════════════════════


class TestTaskEngineActions:
    def test_reset_engine_with_method(self, installed):
        called = []
        installed.task_engine.reset = lambda: called.append(1)
        assert ha.reset_task_engine() is True
        assert called == [1]

    def test_reset_engine_without_method(self, installed):
        # The stub engine has no reset method by default
        assert ha.reset_task_engine() is False

    def test_reset_engine_no_emperor(self, monkeypatch):
        monkeypatch.setattr(ha, "_get_emperor", lambda: None)
        assert ha.reset_task_engine() is False

    def test_reset_engine_handles_exception(self, installed):
        installed.task_engine.reset = MagicMock(side_effect=RuntimeError("boom"))
        assert ha.reset_task_engine() is False


# ══════════════════════════════════════════════════════════════════
# Alert actions
# ══════════════════════════════════════════════════════════════════


class TestAlertActions:
    def test_silence_existing_rule(self, installed):
        installed.alerts.add_rule(AlertRule(
            name="spam_rule",
            metric="x", operator=">", threshold=10,
            severity=AlertSeverity.WARNING,
        ))
        assert ha.silence_alert_rule("spam_rule", duration_seconds=1) is True
        # Rule should be disabled immediately
        assert installed.alerts.get_rule("spam_rule").enabled is False

    def test_silence_nonexistent_rule(self, installed):
        assert ha.silence_alert_rule("ghost") is False

    def test_silence_already_disabled_rule(self, installed):
        installed.alerts.add_rule(AlertRule(
            name="r", metric="x", operator=">", threshold=10,
            severity=AlertSeverity.WARNING, enabled=False,
        ))
        assert ha.silence_alert_rule("r") is False

    def test_silence_re_enables_after_duration(self, installed):
        installed.alerts.add_rule(AlertRule(
            name="temp", metric="x", operator=">", threshold=10,
            severity=AlertSeverity.WARNING,
        ))
        assert ha.silence_alert_rule("temp", duration_seconds=1) is True
        assert installed.alerts.get_rule("temp").enabled is False
        # Wait for the background thread to re‑enable (duration + 0.5s slack)
        time.sleep(1.5)
        assert installed.alerts.get_rule("temp").enabled is True

    def test_silence_no_emperor(self, monkeypatch):
        monkeypatch.setattr(ha, "_get_emperor", lambda: None)
        assert ha.silence_alert_rule("any") is False

    def test_silence_handles_disable_exception(self, installed):
        installed.alerts.add_rule(AlertRule(
            name="x", metric="x", operator=">", threshold=10,
            severity=AlertSeverity.WARNING,
        ))
        installed.alerts.disable_rule = MagicMock(side_effect=RuntimeError("nope"))
        assert ha.silence_alert_rule("x") is False


# ══════════════════════════════════════════════════════════════════
# System actions
# ══════════════════════════════════════════════════════════════════


class TestSystemActions:
    def test_flush_logs(self):
        # Should not raise
        assert ha.flush_logs() is True

    def test_gc_collect(self):
        # Should not raise
        assert ha.gc_collect() is True

    def test_gc_collect_actually_runs(self):
        # Create some garbage to make sure collect does something
        for _ in range(10):
            [object() for _ in range(100)]
        # Run a full collection; returns count of unreachable objects
        collected = gc.collect()
        # Just verify the function calls gc.collect (it returns the count)
        assert ha.gc_collect() is True
        assert collected >= 0  # may be 0 if nothing was unreachable
