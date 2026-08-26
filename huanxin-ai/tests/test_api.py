"""
Tests for HUANXIN API server.
Uses FastAPI TestClient and pytest-asyncio.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from huanxin.api.server import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health & Status
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client: TestClient):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    def test_status_returns_fields(self, client: TestClient):
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        for key in ["name", "version", "uptime_seconds", "domains_loaded"]:
            assert key in data, f"Missing key: {key}"


class TestTokenAuth:
    def test_health_open_with_token_set(self, monkeypatch):
        monkeypatch.setenv("HUANXIN_API_TOKEN", "abc")
        from huanxin.api.server import app
        c = TestClient(app)
        # /health 始终放行
        assert c.get("/health").status_code == 200
        # 无令牌访问受保护路由 → 401
        assert c.get("/status").status_code == 401
        # Bearer 正确 → 200
        assert c.get("/status", headers={"Authorization": "Bearer abc"}).status_code == 200


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

class TestDomains:
    def test_list_domains_returns_list(self, client: TestClient):
        response = client.get("/domains")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_domain_entry_structure(self, client: TestClient):
        response = client.get("/domains")
        data = response.json()
        if data:
            entry = data[0]
            assert "name" in entry
            assert "capabilities" in entry


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

class TestExecute:
    def test_execute_empty_command_rejected(self, client: TestClient):
        response = client.post("/execute", json={"command": ""})
        assert response.status_code == 400

    def test_execute_no_orchestrator_returns_error(self, client: TestClient):
        """When orchestrator is not initialized, return error in response body."""
        response = client.post("/execute", json={"command": "hello"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Orchestrator" in data["error"]

    def test_execute_response_structure(self, client: TestClient):
        """Even on failure, response follows the schema."""
        response = client.post("/execute", json={"command": "hello world"})
        data = response.json()
        for key in ["success", "domain", "error", "execution_time_ms", "timestamp"]:
            assert key in data, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

class TestMemory:
    def test_memory_search_no_orchestrator(self, client: TestClient):
        response = client.get("/memory/search")
        assert response.status_code == 200
        assert response.json() == []

    def test_memory_search_with_query(self, client: TestClient):
        response = client.get("/memory/search", params={"query": "test"})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Evolution
# ---------------------------------------------------------------------------

class TestEvolution:
    def test_evolution_report_no_orchestrator(self, client: TestClient):
        response = client.get("/evolution/report")
        assert response.status_code == 200
        data = response.json()
        assert data["total_cycles"] == 0


# ---------------------------------------------------------------------------
# WebSocket (HTTP upgrade test)
# ---------------------------------------------------------------------------

class TestWebSocket:
    def test_ws_endpoint_exists(self, client: TestClient):
        """WebSocket endpoint is defined in the app."""
        # TestClient can't fully negotiate WS; check the route is registered
        routes = [r.path for r in app.routes]
        assert "/ws" in routes
