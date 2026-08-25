"""Tests for jarvis.dashboard_html and dashboard API endpoints."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from jarvis.court.court import Court
from jarvis.court_api import create_app


def _login(client):
    """以种子管理员登录并返回会话 token（强制会话登录中间件要求）。"""
    r = client.post(
        "/api/auth/login",
        json={
            "username": os.environ["EMPEROR_ADMIN_USER"],
            "password": os.environ["EMPEROR_ADMIN_PASS"],
        },
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


# ══════════════════════════════════════════════════════════════════
# Dashboard HTML
# ══════════════════════════════════════════════════════════════════


class TestDashboardHtml:
    def test_generate_html_returns_html(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        assert "<!DOCTYPE html>" in html
        assert "<title>Emperor Dashboard</title>" in html
        assert "Emperor Dashboard" in html

    def test_generate_html_injects_api_base(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html(api_base="http://localhost:9999")
        assert "http://localhost:9999" in html
        assert "var API = " in html

    def test_generate_html_is_self_contained(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        # No external resource references
        assert 'src="http' not in html
        assert 'href="http' not in html
        # Contains inline styles and script
        assert "<style>" in html
        assert "<script>" in html


# ══════════════════════════════════════════════════════════════════
# Dashboard API endpoint
# ══════════════════════════════════════════════════════════════════


class TestDashboardApi:
    @pytest.fixture
    def client(self):
        court = Court()
        app = create_app(court=court)
        app.extra["host"] = "127.0.0.1"
        app.extra["port"] = 9999
        # 强制会话登录：先以种子管理员登录再注入 Bearer 头
        c = TestClient(app)
        c.headers["Authorization"] = f"Bearer {_login(c)}"
        return c

    def test_dashboard_returns_html(self, client):
        # /dashboard 现服务于 ChatGPT 式对话大盘；监控大盘（标题 "Emperor Dashboard"）
        # 在 /dashboard/legacy 路由，故此处断言 legacy 大盘。
        resp = client.get("/dashboard/legacy")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Emperor Dashboard" in resp.text
        assert "127.0.0.1:9999" in resp.text

    def test_dashboard_status_empty_court(self, client):
        resp = client.get("/dashboard/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "court" in data
        assert "ministers" in data
        assert "tasks" in data
        assert "config" in data
        assert data["court"]["active_ministers"] >= 0
        assert data["court"]["cycle"] >= 0
        assert isinstance(data["ministers"], list)
        assert data["scheduler_running"] is False

    def test_dashboard_status_with_ministers(self, client):
        # Register some ministers
        client.post("/court/register", json={
            "name": "alice", "domain": "math", "temperature": 0.5,
        })
        client.post("/court/register", json={
            "name": "bob", "domain": "science", "temperature": 0.7,
        })
        client.post("/court/register", json={
            "name": "carol", "domain": "math", "temperature": 0.9,
        })

        resp = client.get("/dashboard/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["court"]["active_ministers"] == 3
        ministers = data["ministers"]
        assert len(ministers) == 3
        names = [m["name"] for m in ministers]
        assert "alice" in names
        assert "bob" in names
        assert "carol" in names

    def test_dashboard_status_sorted_by_merit(self, client):
        from jarvis.court.court import CourtConfig
        court = Court(config=CourtConfig(min_ministers=3))
        court.register("a", domain="math")
        court.register("b", domain="science")
        court.register("c", domain="literature")
        # Simulate some merit by evolving
        court.evolve(2)

        app2 = create_app(court=court)
        app2.extra["host"] = "127.0.0.1"
        app2.extra["port"] = 9999
        cli = TestClient(app2)
        cli.headers["Authorization"] = f"Bearer {_login(cli)}"

        resp = cli.get("/dashboard/status")
        data = resp.json()
        ministers = data["ministers"]
        # Sorted descending by merit
        merits = [m["merit"] for m in ministers]
        assert merits == sorted(merits, reverse=True)

    def test_dashboard_status_scheduler_info(self, client):
        # By default no scheduler info
        resp = client.get("/dashboard/status")
        data = resp.json()
        assert data["scheduler_running"] is False
        assert data["scheduler_jobs"] == 0
        assert data["scheduler_total_runs"] == 0


# ══════════════════════════════════════════════════════════════════
# Dashboard Export API
# ══════════════════════════════════════════════════════════════════


class TestDashboardExport:
    @pytest.fixture
    def client(self):
        court = Court()
        app = create_app(court=court)
        app.extra["host"] = "127.0.0.1"
        app.extra["port"] = 9999
        # 强制会话登录：先以种子管理员登录再注入 Bearer 头
        c = TestClient(app)
        c.headers["Authorization"] = f"Bearer {_login(c)}"
        return c

    def test_export_returns_json(self, client):
        resp = client.get("/api/dashboard/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "exported_at" in data
        assert isinstance(data["exported_at"], int)
        assert "snapshot" in data
        assert "ministers" in data
        assert "tasks" in data
        assert "alerts" in data
        assert "healing" in data
        assert "config" in data

    def test_export_has_expected_keys(self, client):
        resp = client.get("/api/dashboard/export")
        data = resp.json()
        # Snapshot substructure
        snap = data["snapshot"]
        assert "active_ministers" in snap
        assert "total_ministers" in snap
        assert "cycle" in snap
        # Tasks substructure
        tasks = data["tasks"]
        assert "total" in tasks
        assert "completed" in tasks
        assert "failed" in tasks
        assert "success_rate" in tasks
        # Ministers is a list
        assert isinstance(data["ministers"], list)
        # Config has expected keys
        assert "min_ministers" in data["config"]
        assert "max_ministers" in data["config"]

    def test_export_reflects_empty_court(self, client):
        resp = client.get("/api/dashboard/export")
        data = resp.json()
        assert data["snapshot"]["active_ministers"] == 0
        assert data["tasks"]["total"] == 0
        assert data["ministers"] == []

    def test_export_reflects_registered_ministers(self, client):
        client.post("/court/register", json={"name": "alice", "domain": "math"})
        client.post("/court/register", json={"name": "bob", "domain": "science"})

        resp = client.get("/api/dashboard/export")
        data = resp.json()
        assert data["snapshot"]["active_ministers"] == 2
        minister_names = [m["name"] for m in data["ministers"]]
        assert "alice" in minister_names
        assert "bob" in minister_names


# ══════════════════════════════════════════════════════════════════
# Dashboard HTML Panel Rendering & Search
# ══════════════════════════════════════════════════════════════════


class TestDashboardHtmlPanels:
    def test_all_core_panels_present(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        # All major panel IDs should be present
        expected_panels = [
            "panel-health",
            "panel-healing",
            "panel-tasks",
            "panel-alerts",
            "panel-evals",
        ]
        for panel_id in expected_panels:
            assert f'id="{panel_id}"' in html, f"Panel {panel_id} missing"

    def test_search_input_present(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        assert 'id="dashboard-search-input"' in html
        assert 'debouncedSearch' in html

    def test_quick_action_bar_present(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        assert 'class="quick-bar"' in html
        assert 'refreshAllPanels' in html
        assert 'collapseAllPanels' in html
        assert 'expandAllPanels' in html
        assert 'exportDashboardData' in html

    def test_drag_drop_initialized(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        assert 'initDragAndDrop' in html
        assert 'draggable-panel' in html
        assert 'savePanelOrder' in html
        assert 'restorePanelOrder' in html

    def test_responsive_breakpoints_present(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        assert 'max-width: 899px' in html
        assert 'min-width: 900px' in html
        assert 'min-width: 1400px' in html

    def test_search_data_structure_validation(self):
        """验证搜索返回的数据结构字段完整性"""
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        # Search renders tasks/evals/audits/healing/context_versions sections
        assert 'sectionName' in html or 'sectionIcon' in html
        # Task search results render description, minister, status
        assert 'item.description' in html or 'item.minister' in html
        # Eval search results render suite, passed, failed
        assert 'item.suite' in html or 'item.passed' in html

    def test_search_panel_jump_function(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        assert 'jumpToPanel' in html
        assert 'scrollIntoView' in html
        assert 'search-jump-link' in html
