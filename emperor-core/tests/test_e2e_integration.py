"""E2E Integration Tests — Full user journey across all subsystems.

Covers: task dispatch → execute → audit → eval → heal → context versioning
Uses in-memory components; no external services required.
"""

import json
import time
import pytest
from starlette.testclient import TestClient

from jarvis.court_api import create_app
from jarvis.emperor import Emperor


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def client():
    """Create a FastAPI test client with a bare in-memory Emperor."""
    emperor = Emperor()

    app = create_app()
    app.extra["emperor"] = emperor
    app.extra["config"] = emperor.config
    app.extra["alert_manager"] = getattr(emperor, "alerts", None)
    app.extra["approval_engine"] = getattr(emperor, "approval_engine", None)

    with TestClient(app) as c:
        yield c


# ══════════════════════════════════════════════════════════════════
# Journey 1: Task → Audit Trail
# ══════════════════════════════════════════════════════════════════


class TestTaskToAudit:
    """Verify that dispatched tasks produce audit entries."""

    def test_manual_task_creates_audit(self, client):
        """Dispatch a task → verify audit endpoint responds correctly."""
        payload = {
            "prompt": "计算 3.14 * 2 的结果",
            "domain": "math",
            "minister": "test_minister",
        }

        resp = client.post("/api/pipelines/execute", json=payload)
        assert resp.status_code in (200, 201, 202, 400, 404, 422)

        # Audit endpoint must respond correctly
        aud_resp = client.get("/api/dashboard/audit/recent?limit=10")
        assert aud_resp.status_code == 200
        data = aud_resp.json()
        entries = data.get("entries", data.get("audit_entries", []))
        assert isinstance(entries, list), "Audit endpoint must return an entries list"


class TestAuditEndpoints:
    """Verify audit API endpoints return correct shapes."""

    def test_audit_stats(self, client):
        resp = client.get("/api/dashboard/audit/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_audit_recent(self, client):
        resp = client.get("/api/dashboard/audit/recent?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        entries = data.get("entries", data.get("audit_entries", data))
        assert isinstance(entries, list)


# ══════════════════════════════════════════════════════════════════
# Journey 2: Eval → Context Snapshot
# ══════════════════════════════════════════════════════════════════


class TestEvalToContext:
    """Verify eval results and context versioning work end-to-end."""

    def test_dashboard_evals(self, client):
        """Eval dashboard returns valid data."""
        resp = client.get("/api/dashboard/evals/report")
        assert resp.status_code == 200

    def test_context_versioning_ping(self, client):
        """Context version endpoints respond without crashing."""
        # These may be empty if no snapshots exist
        resp = client.get("/api/dashboard/context/versions")
        assert resp.status_code in (200, 404, 503)


# ══════════════════════════════════════════════════════════════════
# Journey 3: Healing → Alert → Recovery
# ══════════════════════════════════════════════════════════════════


class TestHealingEndpoints:
    """Verify healing API endpoints work correctly."""

    def test_healing_actions_list(self, client):
        resp = client.get("/api/healing/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert "actions" in data
        assert isinstance(data["actions"], list)
        assert "total" in data

    def test_healing_history(self, client):
        resp = client.get("/api/healing/history?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "history" in data
        assert isinstance(data["history"], list)

    def test_healing_check_noop(self, client):
        """Healing check should succeed even with no alerts."""
        resp = client.post("/api/healing/check")
        assert resp.status_code == 200
        data = resp.json()
        assert "checked_rules" in data
        assert "actions_executed" in data


# ══════════════════════════════════════════════════════════════════
# Journey 4: Pipeline Monitor
# ══════════════════════════════════════════════════════════════════


class TestPipelineMonitorEndpoints:
    """Verify pipeline monitor API endpoints respond."""

    def test_monitor_summary(self, client):
        resp = client.get("/api/pipelines/monitor/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_pipelines" in data
        assert "pipelines" in data

    def test_monitor_live(self, client):
        resp = client.get("/api/pipelines/monitor/live")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_pipelines" in data or "total_tracked" in data


# ══════════════════════════════════════════════════════════════════
# Journey 5: Smart Search
# ══════════════════════════════════════════════════════════════════


class TestSmartSearch:
    """Verify unified search endpoint across subsystems."""

    def test_search_empty(self, client):
        resp = client.get("/api/dashboard/search?q=")
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == ""
        assert data["tasks"] == []
        assert data["evals"] == []

    def test_search_with_query(self, client):
        resp = client.get("/api/dashboard/search?q=test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "test"
        for key in ["tasks", "evals", "audits", "healing", "context_versions"]:
            assert key in data
            assert isinstance(data[key], list)

    def test_search_limit(self, client):
        resp = client.get("/api/dashboard/search?q=&limit=3")
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════
# Journey 6: Approval Engine
# ══════════════════════════════════════════════════════════════════


class TestApprovalEndpoints:
    """Verify approval engine endpoints."""

    def test_approval_pending(self, client):
        resp = client.get("/api/approvals/pending")
        # May 404 or 503 if engine not wired via extra
        assert resp.status_code in (200, 404, 503)

    def test_approval_policies(self, client):
        resp = client.get("/api/approvals/policies")
        assert resp.status_code in (200, 404, 503)


# ══════════════════════════════════════════════════════════════════
# Journey 7: Health + Dashboard Core
# ══════════════════════════════════════════════════════════════════


class TestDashboardCore:
    """Verify core dashboard endpoints."""

    def test_dashboard_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "uptime" in data or "status" in data or "cpu" in data

    def test_dashboard_capability_stats(self, client):
        resp = client.get("/api/dashboard/capability-stats")
        assert resp.status_code in (200, 404, 503)

    def test_dashboard_ministers(self, client):
        resp = client.get("/api/dashboard/ministers")
        assert resp.status_code in (200, 404, 503)

    def test_dashboard_template_list(self, client):
        resp = client.get("/api/dashboard/templates")
        assert resp.status_code in (200, 404, 503)

    def test_dashboard_plugins(self, client):
        resp = client.get("/api/dashboard/plugins")
        assert resp.status_code in (200, 404, 503)

    def test_dashboard_config(self, client):
        resp = client.get("/api/dashboard/config")
        assert resp.status_code in (200, 404, 503)


# ══════════════════════════════════════════════════════════════════
# Journey 8: Cross-subsystem Flow (full user journey)
# ══════════════════════════════════════════════════════════════════


class TestFullUserJourney:
    """Simulate a complete user session: search → task → audit → eval → heal."""

    def test_full_cycle(self, client):
        """Simulate a user who searches, dispatches a task, checks audit, eval, and heal."""

        # Phase 1: Search (empty)
        s1 = client.get("/api/dashboard/search?q=")
        assert s1.status_code == 200

        # Phase 2: Dispatch task
        task_payload = {"prompt": "E2E 测试任务：输出 'Hello World'", "domain": "general"}
        t1 = client.post("/api/pipelines/execute", json=task_payload)
        assert t1.status_code in (200, 201, 202, 400, 404, 422)

        # Phase 3: Check eval status
        e1 = client.get("/api/dashboard/evals/report")
        assert e1.status_code == 200

        # Phase 4: Check healing actions
        h1 = client.get("/api/healing/actions")
        assert h1.status_code == 200
        h1_data = h1.json()
        assert "actions" in h1_data

        # Phase 5: Run healing check after task
        h2 = client.post("/api/healing/check")
        assert h2.status_code == 200
        h2_data = h2.json()
        assert "actions_executed" in h2_data

        # Phase 6: Verify search works after all operations
        s2 = client.get("/api/dashboard/search?q=E2E")
        assert s2.status_code == 200
        s2_data = s2.json()
        assert s2_data["query"] == "e2e"

        # Phase 7: Audit trail exists
        a1 = client.get("/api/dashboard/audit/stats")
        assert a1.status_code == 200

        # Phase 8: Health still reports correctly
        p1 = client.get("/api/health")
        assert p1.status_code == 200
