"""Integration tests for Alerts + Scheduler + Dashboard."""

import time
import pytest
from fastapi.testclient import TestClient

from jarvis.alerts import AlertManager, AlertRule
from jarvis.court.scheduler import Scheduler, SchedulerState


# ══════════════════════════════════════════════════════════════════
# Scheduler _build_state
# ══════════════════════════════════════════════════════════════════


class TestSchedulerBuildState:
    """Scheduler._build_state produces correct metrics for alerts."""

    def test_state_keys(self):
        sched = Scheduler()
        # Without emperor, _build_state won't have court data
        # but the method itself should be callable with attr access
        # We test with emperor later

    def test_state_with_emperor(self):
        from jarvis.emperor import Emperor
        emp = Emperor()
        emp.register("test_m", domain="math")

        sched = Scheduler(emp)
        state = sched._build_state()

        assert "success_rate" in state
        assert "task_failures" in state
        assert "total_tasks" in state
        assert "active_ministers" in state
        assert "total_ministers" in state
        assert "scheduler_running" in state
        assert "total_jobs" in state
        assert "job_failures" in state

        # Scheduler not started yet
        assert state["scheduler_running"] == 0
        # All keys must have valid types
        assert isinstance(state["total_ministers"], int)
        assert isinstance(state["total_tasks"], int)

    def test_state_scheduler_running(self):
        from jarvis.emperor import Emperor
        emp = Emperor()
        emp.register("test_m2", domain="math")

        sched = Scheduler(emp)
        sched.add_job("ping", lambda: None, 999.0)
        sched.start()
        time.sleep(0.1)

        state = sched._build_state()
        assert state["scheduler_running"] == 1
        assert state["total_jobs"] == 1

        sched.stop()

    def test_state_after_task_failure(self):
        from jarvis.emperor import Emperor
        emp = Emperor()
        emp.register("fail_m", domain="test")

        sched = Scheduler(emp)

        def fail_job():
            raise RuntimeError("test failure")

        sched.add_job("failer", fail_job, 0.05)
        sched.start()
        time.sleep(0.3)
        sched.stop()

        state = sched._build_state()
        assert state["job_failures"] >= 1


# ══════════════════════════════════════════════════════════════════
# Scheduler alert auto-evaluation
# ══════════════════════════════════════════════════════════════════


class TestSchedulerAlertIntegration:
    """AlertManager auto-evaluates each scheduler tick."""

    def test_alert_fires_during_tick(self):
        from jarvis.emperor import Emperor
        emp = Emperor()
        emp.register("alice", domain="math")

        mgr = AlertManager()
        mgr.add_rule(AlertRule(
            "no_tasks", "total_tasks", 1, "eq",
            severity="info", message="No tasks yet",
            cooldown_seconds=0,
        ))

        sched = Scheduler(emp)
        sched._alert_manager = mgr

        # Fire one tick manually — should fire the "no_tasks" rule
        # since total_tasks == 0 (eq 1 is false) ... wait let's adjust
        # Actually, let's add a dummy job to trigger tick cycle
        mgr2 = AlertManager()
        mgr2.add_rule(AlertRule(
            "sched_on", "scheduler_running", 1, "eq",
            severity="info", message="Scheduler is running",
            cooldown_seconds=0,
        ))

        sched2 = Scheduler(emp)
        sched2._alert_manager = mgr2
        sched2.add_job("dummy", lambda: None, 999.0)
        sched2.start()
        # After tick, scheduler_running should be 1
        time.sleep(0.2)
        sched2.stop()

        history = mgr2.history()
        assert any(a.rule_name == "sched_on" for a in history)

    def test_no_evaluate_when_no_alert_manager(self):
        from jarvis.emperor import Emperor
        emp = Emperor()
        sched = Scheduler(emp)

        # Without alert_manager, _tick should run without error
        sched._tick()  # should not raise


# ══════════════════════════════════════════════════════════════════
# Dashboard /alerts endpoint
# ══════════════════════════════════════════════════════════════════


class TestDashboardAlertsAPI:
    """GET /dashboard/alerts returns alert data."""

    def test_alerts_endpoint_returns_history(self):
        from jarvis.emperor import Emperor
        emp = Emperor()
        mgr = emp.alerts
        mgr.add_rule(AlertRule("test", "x", 0, "lt", cooldown_seconds=0))
        mgr.evaluate({"x": -1})

        app = emp.app
        app.extra["alert_manager"] = mgr
        client = TestClient(app)
        resp = client.get("/dashboard/alerts")
        assert resp.status_code == 200

        data = resp.json()
        assert "history" in data
        assert "rules" in data
        assert len(data["history"]) >= 1
        assert data["history"][0]["rule_name"] == "test"

    def test_alerts_endpoint_no_manager(self):
        from jarvis.emperor import Emperor
        emp = Emperor()
        app = emp.app
        app.extra.pop("alert_manager", None)
        client = TestClient(app)
        resp = client.get("/dashboard/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"history": [], "rules": []}

    def test_alerts_endpoint_rules_list(self):
        from jarvis.emperor import Emperor
        emp = Emperor()
        mgr = emp.alerts
        mgr.add_rule(AlertRule("r1", "m1", 10, "gt", "warning", "High!"))
        mgr.add_rule(AlertRule("r2", "m2", 5, "lt", "info", "Low!"))

        app = emp.app
        app.extra["alert_manager"] = mgr
        client = TestClient(app)
        resp = client.get("/dashboard/alerts")
        assert resp.status_code == 200

        data = resp.json()
        rules = data["rules"]
        assert len(rules) == 2
        rule_names = [r["name"] for r in rules]
        assert "r1" in rule_names
        assert "r2" in rule_names

    def test_dashboard_status_includes_alerts_extra(self):
        from jarvis.emperor import Emperor
        emp = Emperor()

        app = emp.app
        app.extra["alert_manager"] = emp.alerts
        client = TestClient(app)
        resp = client.get("/dashboard/status")
        assert resp.status_code == 200
        # Alert manager is injected but status endpoint returns court+task data
        data = resp.json()
        assert "court" in data
