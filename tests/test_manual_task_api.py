"""Tests for POST /api/manual_task endpoint (inline task form)."""

import pytest
from fastapi.testclient import TestClient

from jarvis.court_api import create_app
from jarvis.emperor import Emperor


# ══════════════════════════════════════════════════════════════════
# Fixture
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def client():
    app = create_app()
    emperor = Emperor()
    emperor.register("turing", domain="math")
    emperor.register("ada", domain="code")
    app.extra["emperor"] = emperor
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════


class TestManualTask:
    def test_submit_valid_task(self, client):
        """POST /api/manual_task with valid prompt returns report + id."""
        res = client.post("/api/manual_task", json={
            "prompt": "1+1等于几",
            "domain": "math",
        })
        assert res.status_code == 200
        data = res.json()
        assert "report" in data
        assert "id" in data
        assert len(data["id"]) > 0

    def test_empty_prompt_returns_400(self, client):
        """POST /api/manual_task with empty prompt returns 400."""
        res = client.post("/api/manual_task", json={
            "prompt": "",
            "domain": "general",
        })
        assert res.status_code == 400

    def test_prompt_without_domain_defaults_general(self, client):
        """POST /api/manual_task without domain uses general."""
        res = client.post("/api/manual_task", json={
            "prompt": "hello",
        })
        assert res.status_code == 200
        data = res.json()
        assert "report" in data

    def test_different_domain_code(self, client):
        """POST /api/manual_task with code domain works."""
        res = client.post("/api/manual_task", json={
            "prompt": "计算文件行数",
            "domain": "code",
        })
        assert res.status_code == 200
        data = res.json()
        assert "report" in data
        assert "id" in data

    def test_report_contains_capability_output(self, client):
        """When prompt matches a capability, report should contain [能力结果: 能力名]."""
        res = client.post("/api/manual_task", json={
            "prompt": "现在几点了？今天是星期几？",
            "domain": "general",
        })
        assert res.status_code == 200
        data = res.json()
        assert "report" in data
        # datetime capability should match
        assert "能力结果" in data["report"] or "datetime" in data["report"].lower()

    def test_chinese_prompt_works(self, client):
        """Chinese prompts are handled correctly."""
        res = client.post("/api/manual_task", json={
            "prompt": "生成一个UUID",
            "domain": "general",
        })
        assert res.status_code == 200
        data = res.json()
        assert "report" in data
        assert len(data["report"]) > 0

    def test_no_emperor_returns_503(self):
        """When emperor is not available, returns 503."""
        app = create_app()
        from fastapi.testclient import TestClient as TC
        c = TC(app)
        res = c.post("/api/manual_task", json={
            "prompt": "test",
            "domain": "general",
        })
        assert res.status_code == 503
