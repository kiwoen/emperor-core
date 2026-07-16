"""Tests for the Court REST API (FastAPI test client).

Validates all endpoints: registration, evolution, inspection,
dispatch recording, feedback, and genome persistence.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.court_api import app
from jarvis.court.court import CourtConfig


@pytest.fixture
def client():
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════
# Health
# ══════════════════════════════════════════════════════════════════

class TestHealth:
    def test_root(self, client):
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_summary_empty(self, client):
        r = client.get("/court/summary")
        assert r.status_code == 200
        assert "summary" in r.json()

    def test_snapshot_empty(self, client):
        r = client.get("/court/snapshot")
        assert r.status_code == 200
        assert r.json()["total_ministers"] == 0


# ══════════════════════════════════════════════════════════════════
# Registration
# ══════════════════════════════════════════════════════════════════

class TestAPIRegistration:
    def test_register_single(self, client):
        r = client.post("/court/register", json={
            "name": "curie", "domain": "physics", "temperature": 0.3,
        })
        assert r.status_code == 200
        assert r.json()["name"] == "curie"

    def test_register_auto_name(self, client):
        r = client.post("/court/register", json={"domain": "code"})
        assert r.status_code == 200
        assert r.json()["name"].startswith("m")

    def test_register_batch(self, client):
        r = client.post("/court/register/batch", json={
            "ministers": [
                {"domain": "math"},
                {"name": "turing", "domain": "code"},
            ],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        assert "turing" in data["names"]
        # auto-named minister starts with "m"
        other = [n for n in data["names"] if n != "turing"][0]
        assert other.startswith("m")

    def test_list_ministers_after_register(self, client):
        client.post("/court/register", json={"name": "einstein", "domain": "physics"})
        r = client.get("/court/ministers")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1

    def test_minister_detail(self, client):
        client.post("/court/register", json={"name": "newton", "domain": "physics"})
        r = client.get("/court/minister/newton")
        assert r.status_code == 200
        assert "newton" in r.json()["detail"]

    def test_minister_not_found(self, client):
        r = client.get("/court/minister/nobody")
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════════════
# Evolution
# ══════════════════════════════════════════════════════════════════

class TestAPIEvolution:
    def test_evolve_single(self, client):
        client.post("/court/register/batch", json={
            "ministers": [
                {"domain": "math"},
                {"domain": "code"},
                {"name": "curie", "domain": "physics"},
            ],
        })
        r = client.post("/court/evolve", json={"cycles": 1})
        assert r.status_code == 200
        data = r.json()
        assert data["total_cycles"] == 1

    def test_evolve_multiple(self, client):
        client.post("/court/register/batch", json={
            "ministers": [
                {"domain": "math"},
                {"domain": "code"},
                {"name": "curie", "domain": "physics"},
            ],
        })
        r = client.post("/court/evolve", json={"cycles": 3})
        assert r.status_code == 200
        data = r.json()
        assert data["total_cycles"] == 3
        assert len(data["cycles"]) == 3

    def test_history_after_evolution(self, client):
        client.post("/court/register/batch", json={
            "ministers": [
                {"domain": "math"},
                {"domain": "code"},
                {"name": "curie", "domain": "physics"},
            ],
        })
        client.post("/court/evolve", json={"cycles": 2})
        r = client.get("/court/history")
        assert r.status_code == 200
        data = r.json()
        assert data["cycles"] >= 2


# ══════════════════════════════════════════════════════════════════
# Dispatch & Feedback
# ══════════════════════════════════════════════════════════════════

class TestAPIDispatch:
    def test_record_dispatch(self, client):
        client.post("/court/register", json={"name": "alpha", "domain": "math"})
        r = client.post("/court/dispatch", json={
            "minister": "alpha",
            "edict_id": "e1",
            "intent": "solve equation",
            "success": True,
            "confidence": 0.95,
            "execution_time_ms": 120.0,
        })
        assert r.status_code == 200

    def test_record_feedback(self, client):
        client.post("/court/register", json={"name": "beta", "domain": "code"})
        client.post("/court/dispatch", json={
            "minister": "beta",
            "edict_id": "e2",
            "intent": "write function",
            "success": True,
            "confidence": 0.88,
        })
        r = client.post("/court/feedback", json={
            "minister": "beta", "edict_id": "e2", "score": 0.95,
        })
        assert r.status_code == 200

    def test_feedback_not_found(self, client):
        r = client.post("/court/feedback", json={
            "minister": "ghost", "edict_id": "nx", "score": 0.5,
        })
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════════════
# Genome persistence
# ══════════════════════════════════════════════════════════════════

class TestAPIPersistence:
    def test_save_no_path_configured(self, client):
        client.post("/court/register", json={"name": "x", "domain": "math"})
        r = client.post("/court/genomes/save")
        assert r.status_code == 400


# ══════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════

class TestAPIConfig:
    def test_config_endpoint_default(self, client):
        r = client.get("/court/config")
        assert r.status_code == 200
        data = r.json()
        assert "configured" in data
        assert "genome_path" in data

    def test_load_config_missing_file(self, client):
        r = client.post("/court/config/load", json={"path": "nonexistent.yaml"})
        assert r.status_code == 404

    def test_load_config_valid(self, client, tmp_path):
        path = tmp_path / "test.yaml"
        path.write_text("elitism_count: 5\ncrossover_rate: 0.8\n")
        r = client.post("/court/config/load", json={"path": str(path)})
        assert r.status_code == 200
        assert "Config loaded" in r.json()["message"]


# ══════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════

class TestAPIValidation:
    def test_evolve_zero_cycles_rejected(self, client):
        r = client.post("/court/evolve", json={"cycles": 0})
        assert r.status_code == 422

    def test_temperature_out_of_range(self, client):
        r = client.post("/court/register", json={
            "name": "hot", "temperature": 99.0,
        })
        assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════
# Dashboard — task/alert history filtering & export
# ══════════════════════════════════════════════════════════════════

class TestDashboardHistory:
    def test_task_history_no_db_returns_note(self, client):
        """When DB is not initialized, return empty with note."""
        r = client.get("/dashboard/task-history")
        assert r.status_code == 200
        data = r.json()
        assert data["history"] == []
        assert "Database not initialized" in data["note"]

    def test_task_history_accepts_filter_params(self, client):
        """task-history endpoint accepts minister/status/search/limit/offset."""
        r = client.get(
            "/dashboard/task-history"
            "?minister=turing&status=completed&search=hello&limit=10&offset=0"
        )
        assert r.status_code == 200

    def test_alert_history_no_db_returns_note(self, client):
        """When DB is not initialized, return empty with note."""
        r = client.get("/dashboard/alert-history")
        assert r.status_code == 200
        data = r.json()
        assert data["history"] == []
        assert "Database not initialized" in data["note"]

    def test_alert_history_accepts_filter_params(self, client):
        """alert-history endpoint accepts level/search/limit/offset."""
        r = client.get(
            "/dashboard/alert-history"
            "?level=WARNING&search=memory&limit=10&offset=0"
        )
        assert r.status_code == 200

    def test_export_no_db_returns_503(self, client):
        """Export without DB returns 503."""
        r = client.get("/dashboard/export")
        assert r.status_code == 503

    def test_export_accepts_format_params(self, client):
        """Export endpoint accepts format and what params (503 when no DB)."""
        r = client.get("/dashboard/export?format=csv&what=tasks")
        # 503 because no DB, but params are accepted
        assert r.status_code == 503


# ══════════════════════════════════════════════════════════════════
# Dashboard — export with real database
# ══════════════════════════════════════════════════════════════════

class TestDashboardExportWithDB:
    """Tests that require a DB instance wired into app.extra."""

    @pytest.fixture
    def client_with_db(self):
        import tempfile
        import os
        from jarvis.database import Database

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = Database(path)

        from jarvis.court_api import create_app
        app_with_db = create_app()
        app_with_db.extra["db"] = db

        # Seed some data
        db.save_task("t1", "hello world", "turing", "ok", 0.9, "completed")
        db.save_task("t2", "write code", "curie", "ok", 0.8, "failed")
        db.save_evolution(1, "turing", 0.5, 0.8, 0.3)
        db.save_alert("rule1", "WARNING", "memory alert")

        with TestClient(app_with_db) as client:
            yield client

        db.close()
        try:
            os.unlink(path)
        except OSError:
            pass

    def test_export_json_all(self, client_with_db):
        """Export JSON with all data."""
        r = client_with_db.get("/dashboard/export?format=json&what=all")
        assert r.status_code == 200
        data = r.json()
        assert len(data["tasks"]) == 2
        assert len(data["evolutions"]) == 1
        assert len(data["alerts"]) == 1

    def test_export_json_tasks_only(self, client_with_db):
        """Export JSON with tasks only."""
        r = client_with_db.get("/dashboard/export?format=json&what=tasks")
        assert r.status_code == 200
        data = r.json()
        assert "tasks" in data
        assert "alerts" not in data
        assert "evolutions" not in data
        assert len(data["tasks"]) == 2

    def test_export_csv_all(self, client_with_db):
        """Export CSV with all data."""
        r = client_with_db.get("/dashboard/export?format=csv&what=all")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        content = r.text
        # Should contain header rows and separator
        assert "task_id" in content
        assert "rule_name" in content
        assert "minister_name" in content

    def test_export_csv_alerts_only(self, client_with_db):
        """Export CSV with alerts only."""
        r = client_with_db.get("/dashboard/export?format=csv&what=alerts")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        content = r.text
        assert "rule_name" in content
        assert "memory alert" in content

    def test_task_history_with_filters(self, client_with_db):
        """task-history endpoint applies DB filters."""
        r = client_with_db.get("/dashboard/task-history?minister=turing")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["history"][0]["minister"] == "turing"

    def test_alert_history_with_filters(self, client_with_db):
        """alert-history endpoint applies DB filters."""
        r = client_with_db.get("/dashboard/alert-history?level=WARNING")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["history"][0]["level"] == "WARNING"


# ══════════════════════════════════════════════════════════════════
# Theme API
# ══════════════════════════════════════════════════════════════════

class TestThemeAPI:
    def test_get_config_returns_theme(self, client):
        """GET /api/config returns theme field."""
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "theme" in data
        assert data["theme"] in ("dark", "light", "auto")

    def test_get_config_returns_refresh_interval(self, client):
        """GET /api/config returns refresh_interval_seconds."""
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "refresh_interval_seconds" in data
        assert isinstance(data["refresh_interval_seconds"], (int, float))

    def test_set_theme_dark(self, client):
        """POST /api/theme sets theme to dark."""
        r = client.post("/api/theme", json={"theme": "dark"})
        assert r.status_code == 200
        data = r.json()
        assert data["theme"] == "dark"
        assert data["status"] == "ok"

    def test_set_theme_light(self, client):
        """POST /api/theme sets theme to light."""
        r = client.post("/api/theme", json={"theme": "light"})
        assert r.status_code == 200
        data = r.json()
        assert data["theme"] == "light"
        assert data["status"] == "ok"

    def test_set_theme_auto(self, client):
        """POST /api/theme sets theme to auto."""
        r = client.post("/api/theme", json={"theme": "auto"})
        assert r.status_code == 200
        data = r.json()
        assert data["theme"] == "auto"
        assert data["status"] == "ok"

    def test_set_theme_invalid_rejected(self, client):
        """POST /api/theme rejects invalid theme values."""
        for bad in ["red", "blue", "day", "night", "", "system"]:
            r = client.post("/api/theme", json={"theme": bad})
            assert r.status_code == 400, f"Expected 400 for theme={bad!r}, got {r.status_code}"

    def test_set_theme_defaults_to_dark(self, client):
        """POST /api/theme with no body defaults to dark."""
        r = client.post("/api/theme", json={})
        assert r.status_code == 200
        assert r.json()["theme"] == "dark"

    def test_theme_config_integration(self, client):
        """Set theme then read from /api/config (in-memory only for test client)."""
        r = client.post("/api/theme", json={"theme": "auto"})
        assert r.status_code == 200

        # Config returns default theme since test client has no emperor injected.
        # But endpoint should still return 200 and a valid theme.
        r2 = client.get("/api/config")
        assert r2.status_code == 200
        cfg = r2.json()
        assert "theme" in cfg


class TestHealthEndpoint:
    def test_health_endpoint(self, client):
        """GET /api/health 返回健康数据"""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "cpu_percent" in data
        assert "memory" in data
        assert "disk" in data
        assert "uptime" in data
