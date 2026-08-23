"""Integration tests for Emperor + built-in plugins."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.emperor import Emperor, EmperorConfig
from jarvis.plugin import LifecycleEvent, Plugin
from jarvis.plugins import LoggingPlugin, MetricsPlugin


# ══════════════════════════════════════════════════════════════════
# Emperor.metrics (auto register)
# ══════════════════════════════════════════════════════════════════

class TestEmperorMetricsLazy:
    """Emperor.metrics property auto-creates and registers MetricsPlugin."""

    def test_metrics_property_returns_plugin(self):
        emp = Emperor()
        mp = emp.metrics
        assert mp is not None
        assert mp.name == "MetricsPlugin"

    def test_metrics_idempotent(self):
        emp = Emperor()
        mp1 = emp.metrics
        mp2 = emp.metrics
        assert mp1 is mp2

    def test_metrics_plugin_registered_via_plugins(self):
        emp = Emperor()
        mp = emp.metrics
        # Should be visible in the plugin manager
        found = any(p.name == "MetricsPlugin"
                    for p in emp.plugins._plugins)
        assert found

    def test_metrics_collects_task_execution(self):
        emp = Emperor()
        emp.register("alice", domain="math")
        emp.execute_task("2+2?", domain="math")
        mp = emp.metrics
        s = mp.summary()
        assert s.total_tasks == 1

    def test_metrics_collects_evolution(self):
        emp = Emperor()
        emp.register("alice")
        emp.evolve(cycles=2)
        mp = emp.metrics
        s = mp.summary()
        assert s.total_evolution_cycles == 1


class TestEmperorMetricsWithLoggingPlugin:
    """Both MetricsPlugin and LoggingPlugin can coexist."""

    def test_both_plugins_registered(self, tmp_path):
        log_path = str(tmp_path / "test.jsonl")
        emp = Emperor()
        log_plugin = LoggingPlugin(log_path=log_path)
        emp.plugins.register(log_plugin)
        # Touch metrics to register it
        _ = emp.metrics
        names = [p.name for p in emp.plugins._plugins]
        assert "MetricsPlugin" in names
        assert "LoggingPlugin" in names

    def test_both_plugins_capture(self, tmp_path):
        log_path = str(tmp_path / "cap.jsonl")
        emp = Emperor()
        log_plugin = LoggingPlugin(log_path=log_path, include_kwargs=True)
        emp.plugins.register(log_plugin)
        emp.register("bob")
        emp.execute_task("hello world")
        emp.shutdown()
        # LoggingPlugin should have written events
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) >= 2  # at least STARTUP-like and SHUTDOWN
        events = [json.loads(l)["event"] for l in lines]
        assert any("TASK" in e for e in events)
        assert "SHUTDOWN" in events
        # MetricsPlugin should have captured the task
        assert emp.metrics.summary().total_tasks == 1

    def test_when_metrics_registered_externally_first(self):
        """Emperor eagerly registers MetricsPlugin on init, so
        user-registered plugins coexist alongside the default one."""
        emp = Emperor()
        # The default MetricsPlugin is already registered on init
        assert emp._metrics_plugin is not None
        user_mp = MetricsPlugin(max_samples=42)
        emp.plugins.register(user_mp)
        # emp.metrics returns the eagerly-registered instance
        assert emp.metrics is emp._metrics_plugin
        assert emp.metrics._max_samples == 1000  # default, not 42


# ══════════════════════════════════════════════════════════════════
# Emperor.app + /dashboard/metrics
# ══════════════════════════════════════════════════════════════════

class TestDashboardMetricsEndpoint:
    """Verify /dashboard/metrics returns correct shape."""

    @pytest.fixture
    def client_with_tasks(self):
        emp = Emperor()
        emp.register("alice", "math")
        emp.register("bob", "math")
        # Touch metrics so it's wired in app.extra
        _ = emp.metrics
        # Execute some tasks
        emp.execute_task("2+2?", domain="math")
        emp.execute_task("3+5?", domain="math")
        emp.evolve(cycles=1)
        app = emp.app
        app.extra["metrics_plugin"] = emp._metrics_plugin
        return TestClient(app)

    def test_metrics_summary_nonempty(self, client_with_tasks):
        resp = client_with_tasks.get("/dashboard/metrics")
        assert resp.status_code == 200
        data = resp.json()
        s = data["summary"]
        assert s["total_tasks"] >= 1
        assert s["success_rate"] >= 0.0
        assert s["samples_in_buffer"] >= 1
        assert len(data["tasks"]) >= 1
        assert len(data["evolutions"]) >= 1

    def test_metrics_task_fields(self, client_with_tasks):
        resp = client_with_tasks.get("/dashboard/metrics")
        data = resp.json()
        task_sample = data["tasks"][0]
        for field in ["task_id", "timestamp", "success",
                      "confidence", "execution_time_ms",
                      "domain", "error"]:
            assert field in task_sample

    def test_metrics_evolution_fields(self, client_with_tasks):
        resp = client_with_tasks.get("/dashboard/metrics")
        data = resp.json()
        evo_sample = data["evolutions"][0]
        for field in ["timestamp", "cycles",
                      "active_ministers", "avg_merit"]:
            assert field in evo_sample

    def test_metrics_when_no_plugin(self):
        # Without touching metrics, the endpoint returns empty
        emp = Emperor()
        app = emp.app
        app.extra["metrics_plugin"] = None
        client = TestClient(app)
        resp = client.get("/dashboard/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
        assert data["evolutions"] == []
