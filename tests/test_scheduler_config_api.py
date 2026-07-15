"""Tests for Scheduler configuration API endpoints."""

import pytest
from fastapi.testclient import TestClient

from jarvis.court_api import _scheduler_config, create_app


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════


def _reset_config() -> None:
    _scheduler_config.update({
        "evolve_interval_minutes": 5,
        "task_interval_minutes": 3,
        "auto_schedule": True,
    })


@pytest.fixture(autouse=True)
def _reset_scheduler_config():
    _reset_config()


# ══════════════════════════════════════════════════════════════════
# Fixture
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════
# GET /api/scheduler/config
# ══════════════════════════════════════════════════════════════════


class TestGetSchedulerConfig:
    def test_returns_default_config(self, client):
        """GET /api/scheduler/config returns default values."""
        res = client.get("/api/scheduler/config")
        assert res.status_code == 200
        data = res.json()
        assert data["evolve_interval_minutes"] == 5
        assert data["task_interval_minutes"] == 3
        assert data["auto_schedule"] is True

    def test_returns_all_three_fields(self, client):
        """Response contains exactly 3 config fields."""
        res = client.get("/api/scheduler/config")
        assert res.status_code == 200
        data = res.json()
        assert set(data.keys()) == {
            "evolve_interval_minutes", "task_interval_minutes", "auto_schedule",
        }


# ══════════════════════════════════════════════════════════════════
# PUT /api/scheduler/config — happy path
# ══════════════════════════════════════════════════════════════════


class TestPutSchedulerConfig:
    def test_update_all_fields(self, client):
        """PUT updates all three fields."""
        res = client.put("/api/scheduler/config", json={
            "evolve_interval_minutes": 10,
            "task_interval_minutes": 7,
            "auto_schedule": False,
        })
        assert res.status_code == 200
        cfg = res.json()["config"]
        assert cfg["evolve_interval_minutes"] == 10
        assert cfg["task_interval_minutes"] == 7
        assert cfg["auto_schedule"] is False

    def test_update_partial_preserves_others(self, client):
        """PUT with only evolve_interval leaves other fields unchanged."""
        res = client.put("/api/scheduler/config", json={
            "evolve_interval_minutes": 20,
        })
        assert res.status_code == 200
        cfg = res.json()["config"]
        assert cfg["evolve_interval_minutes"] == 20
        assert cfg["task_interval_minutes"] == 3
        assert cfg["auto_schedule"] is True

    def test_toggle_auto_schedule_off(self, client):
        """auto_schedule=false persists."""
        res = client.put("/api/scheduler/config", json={
            "auto_schedule": False,
        })
        assert res.status_code == 200
        assert res.json()["config"]["auto_schedule"] is False

    def test_toggle_auto_schedule_on(self, client):
        """auto_schedule=true after being turned off."""
        client.put("/api/scheduler/config", json={"auto_schedule": False})
        res = client.put("/api/scheduler/config", json={"auto_schedule": True})
        assert res.status_code == 200
        assert res.json()["config"]["auto_schedule"] is True

    def test_get_reflects_put_changes(self, client):
        """GET after PUT returns updated values."""
        client.put("/api/scheduler/config", json={
            "evolve_interval_minutes": 15,
        })
        res = client.get("/api/scheduler/config")
        assert res.status_code == 200
        assert res.json()["evolve_interval_minutes"] == 15

    def test_returns_updated_fields_list(self, client):
        """Response includes which fields were updated."""
        res = client.put("/api/scheduler/config", json={
            "evolve_interval_minutes": 8,
            "auto_schedule": False,
        })
        assert res.status_code == 200
        updated = res.json()["updated"]
        assert "evolve_interval_minutes" in updated
        assert "auto_schedule" in updated


# ══════════════════════════════════════════════════════════════════
# PUT /api/scheduler/config — validation
# ══════════════════════════════════════════════════════════════════


class TestPutSchedulerConfigValidation:
    def test_evolve_interval_below_1_returns_400(self, client):
        """Evolve interval < 1 returns 400."""
        res = client.put("/api/scheduler/config", json={
            "evolve_interval_minutes": 0,
        })
        assert res.status_code == 422  # Pydantic validation

    def test_task_interval_above_1440_returns_400(self, client):
        """Task interval > 1440 returns 422 (Pydantic)."""
        res = client.put("/api/scheduler/config", json={
            "task_interval_minutes": 2000,
        })
        assert res.status_code == 422

    def test_fractional_minutes_returns_400(self, client):
        """Non-integer minutes returns 400."""
        res = client.put("/api/scheduler/config", json={
            "evolve_interval_minutes": 5.5,
        })
        assert res.status_code == 400
        assert "整数" in res.json()["detail"]

    def test_invalid_value_does_not_alter_config(self, client):
        """A rejected PUT does not change the stored config."""
        before = client.get("/api/scheduler/config").json()
        client.put("/api/scheduler/config", json={
            "evolve_interval_minutes": 5.5,
        })
        after = client.get("/api/scheduler/config").json()
        assert after == before

    def test_empty_body_returns_200(self, client):
        """PUT with empty body is a no-op, returns 200."""
        res = client.put("/api/scheduler/config", json={})
        assert res.status_code == 200

    def test_evolve_at_boundary_1(self, client):
        """Evolve interval = 1 is valid."""
        res = client.put("/api/scheduler/config", json={
            "evolve_interval_minutes": 1,
        })
        assert res.status_code == 200

    def test_task_at_boundary_1440(self, client):
        """Task interval = 1440 (24h) is valid."""
        res = client.put("/api/scheduler/config", json={
            "task_interval_minutes": 1440,
        })
        assert res.status_code == 200


# ══════════════════════════════════════════════════════════════════
# Concurrent updates
# ══════════════════════════════════════════════════════════════════


class TestConcurrentSchedulerConfig:
    def test_multiple_sequential_updates(self, client):
        """Sequential PUTs all succeed and latest wins."""
        client.put("/api/scheduler/config", json={
            "evolve_interval_minutes": 12,
        })
        client.put("/api/scheduler/config", json={
            "task_interval_minutes": 6,
        })
        res = client.get("/api/scheduler/config")
        data = res.json()
        assert data["evolve_interval_minutes"] == 12
        assert data["task_interval_minutes"] == 6
