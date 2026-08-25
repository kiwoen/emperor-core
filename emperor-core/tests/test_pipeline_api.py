"""Tests for pipeline store REST API endpoints (GET /api/pipelines, GET /api/pipelines/<id>)."""

import pytest
from fastapi.testclient import TestClient

from huanxin.court_api import app
from huanxin.pipeline_store import pipeline_store


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_store():
    """Reset pipeline store before each test."""
    pipeline_store.clear()
    yield
    pipeline_store.clear()


class TestListPipelines:
    """GET /api/pipelines"""

    def test_empty_store(self, client):
        r = client.get("/api/pipelines")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["records"] == []

    def test_returns_pipelines_newest_first(self, client):
        pipeline_store.add("daily_brief", "pipe-001", "completed", steps=2, total_steps=3, elapsed_ms=1200)
        pipeline_store.add("health_check", "pipe-002", "running", steps=1, total_steps=2, elapsed_ms=300)

        r = client.get("/api/pipelines?limit=10")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert len(data["records"]) == 2
        # newest first
        assert data["records"][0]["pipeline_id"] == "pipe-002"
        assert data["records"][1]["pipeline_id"] == "pipe-001"

    def test_limit_parameter(self, client):
        for i in range(10):
            pipeline_store.add(f"t{i}", f"pipe-{i:03d}", "completed")
        r = client.get("/api/pipelines?limit=3")
        assert r.status_code == 200
        data = r.json()
        assert len(data["records"]) == 3
        assert data["limit"] == 3

    def test_status_filter(self, client):
        pipeline_store.add("t1", "pipe-001", "completed")
        pipeline_store.add("t2", "pipe-002", "failed")
        pipeline_store.add("t3", "pipe-003", "running")
        r = client.get("/api/pipelines?status=completed")
        assert r.status_code == 200
        data = r.json()
        assert len(data["records"]) == 1
        assert data["records"][0]["status"] == "completed"

    def test_record_fields(self, client):
        pipeline_store.add("search_analyze", "pipe-001", "running", steps=2, total_steps=4, elapsed_ms=999.5)
        r = client.get("/api/pipelines?limit=1")
        data = r.json()
        rec = data["records"][0]
        assert rec["template"] == "search_analyze"
        assert rec["pipeline_id"] == "pipe-001"
        assert rec["status"] == "running"
        assert rec["steps"] == 2
        assert rec["total_steps"] == 4
        assert rec["elapsed_ms"] == 999.5
        assert "created_at" in rec


class TestGetPipelineDetail:
    """GET /api/pipelines/<pipeline_id>"""

    def test_existing_pipeline(self, client):
        pipeline_store.add(
            "daily_brief", "pipe-detail-1", "completed",
            steps=3, total_steps=3, elapsed_ms=2500.0,
            step_details=[
                {"step_name": "gather", "status": "success", "elapsed_ms": 800},
                {"step_name": "analyze", "status": "success", "elapsed_ms": 1200},
                {"step_name": "report", "status": "success", "elapsed_ms": 500},
            ],
        )
        r = client.get("/api/pipelines/pipe-detail-1")
        assert r.status_code == 200
        detail = r.json()
        assert detail["pipeline_id"] == "pipe-detail-1"
        assert detail["template"] == "daily_brief"
        assert detail["status"] == "completed"
        assert len(detail["step_details"]) == 3
        assert detail["step_details"][0]["step_name"] == "gather"
        assert detail["step_details"][0]["status"] == "success"

    def test_nonexistent_pipeline(self, client):
        r = client.get("/api/pipelines/no-such-id")
        assert r.status_code == 404

    def test_pipeline_without_step_details(self, client):
        pipeline_store.add("health_check", "pipe-no-steps", "failed", steps=0, total_steps=0, elapsed_ms=100)
        r = client.get("/api/pipelines/pipe-no-steps")
        assert r.status_code == 200
        detail = r.json()
        assert detail["step_details"] == []
        assert detail["status"] == "failed"
