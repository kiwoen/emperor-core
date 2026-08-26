"""Tests for Sandbox Dashboard API and SandboxManager integration."""

import pytest
import asyncio
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def sandbox_manager():
    """Create a fresh SandboxManager for each test."""
    from huanxin.sandbox import SandboxManager
    return SandboxManager(engine="local_subprocess", timeout_seconds=30)


@pytest.fixture
def client(sandbox_manager):
    """Create FastAPI TestClient with sandbox manager injected."""
    from huanxin.court_api import create_app
    app = create_app()
    app.extra["sandbox_manager"] = sandbox_manager
    with TestClient(app) as c:
        yield c


# ── API Status ────────────────────────────────────────────────────

class TestSandboxStatus:
    """GET /api/dashboard/sandbox/status"""

    def test_returns_engine_info(self, client):
        resp = client.get("/api/dashboard/sandbox/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["engine"] == "local_subprocess"
        assert data["timeout_seconds"] == 30
        assert data["network_enabled"] is False
        assert "available_engines" in data
        assert "local_subprocess" in data["available_engines"]

    def test_503_when_no_sandbox_manager(self):
        from huanxin.court_api import create_app
        app = create_app()
        with TestClient(app) as c:
            resp = c.get("/api/dashboard/sandbox/status")
            assert resp.status_code == 503


# ── Python Code Execution ─────────────────────────────────────────

class TestSandboxRun:
    """POST /api/dashboard/sandbox/run"""

    def test_simple_print(self, client):
        resp = client.post("/api/dashboard/sandbox/run", json={
            "code": "print('hello sandbox')",
            "engine": "local_subprocess",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0
        assert "hello sandbox" in data["stdout"]
        assert data["execution_time_ms"] > 0

    def test_math_expression(self, client):
        resp = client.post("/api/dashboard/sandbox/run", json={
            "code": "print(2 + 3 * 4)",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0
        assert "14" in data["stdout"]

    def test_for_loop(self, client):
        resp = client.post("/api/dashboard/sandbox/run", json={
            "code": "for i in range(3):\n    print(f'Item {i}')",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0
        assert "Item 0" in data["stdout"]
        assert "Item 2" in data["stdout"]

    def test_syntax_error(self, client):
        resp = client.post("/api/dashboard/sandbox/run", json={
            "code": "print(undefined_var)",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] != 0
        assert "NameError" in data["stderr"]

    def test_timeout(self, client):
        resp = client.post("/api/dashboard/sandbox/run", json={
            "code": "import time; time.sleep(10)",
            "timeout": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] != 0
        assert "timed out" in data["stderr"].lower()

    def test_empty_code(self, client):
        resp = client.post("/api/dashboard/sandbox/run", json={
            "code": "",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0


# ── Shell Command Execution ───────────────────────────────────────

class TestSandboxShell:
    """POST /api/dashboard/sandbox/shell"""

    def test_echo_command(self, client):
        resp = client.post("/api/dashboard/sandbox/shell", json={
            "command": "echo hello from shell",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "hello" in data["stdout"] and "shell" in data["stdout"]

    def test_shell_error(self, client):
        resp = client.post("/api/dashboard/sandbox/shell", json={
            "command": "nonexistent_command_xyz",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data is not None


# ── Execution History ─────────────────────────────────────────────

class TestSandboxHistory:
    """GET /api/dashboard/sandbox/history"""

    def test_empty_on_start(self, client):
        resp = client.get("/api/dashboard/sandbox/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["history"] == []

    def test_history_after_runs(self, client):
        # Run twice
        client.post("/api/dashboard/sandbox/run", json={"code": "print(1)"})
        client.post("/api/dashboard/sandbox/run", json={"code": "print(2)"})

        resp = client.get("/api/dashboard/sandbox/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        assert len(data["history"]) >= 2
        # Most recent first
        assert "exit_code" in data["history"][0]
        assert "execution_time_ms" in data["history"][0]
        assert "timestamp" in data["history"][0]

    def test_limit_respected(self, client):
        for i in range(5):
            client.post("/api/dashboard/sandbox/run", json={"code": f"print({i})"})

        resp = client.get("/api/dashboard/sandbox/history?limit=3")
        data = resp.json()
        assert len(data["history"]) == 3
        assert data["total"] == 5


# ── Engine Selection ──────────────────────────────────────────────

class TestEngineSelection:
    """Test switching between sandbox engines."""

    def test_local_direct_engine(self, sandbox_manager):
        sm = sandbox_manager
        sm.engine = "local_direct"
        result = asyncio.run(sm.execute_python("print('direct')"))
        assert result.exit_code == 0
        assert "direct" in result.stdout

    def test_unknown_engine_falls_back(self, sandbox_manager):
        sm = sandbox_manager
        sm.engine = "nonexistent"
        result = asyncio.run(sm.execute_python("print('fallback')"))
        assert result.exit_code == 0
        assert "fallback" in result.stdout


# ── SandboxManager Direct ─────────────────────────────────────────

class TestSandboxManagerDirect:
    """Direct SandboxManager unit tests (no HTTP)."""

    def test_execute_python_simple(self, sandbox_manager):
        result = asyncio.run(sandbox_manager.execute_python("print('test')"))
        assert result.exit_code == 0
        assert "test" in result.stdout
        assert result.execution_time_ms > 0

    def test_execute_python_multiline(self, sandbox_manager):
        code = "x = sum(range(100))\nprint(x)"
        result = asyncio.run(sandbox_manager.execute_python(code))
        assert result.exit_code == 0
        assert "4950" in result.stdout

    def test_execution_history_grows(self, sandbox_manager):
        initial = len(sandbox_manager.execution_history)
        asyncio.run(sandbox_manager.execute_python("print('a')"))
        asyncio.run(sandbox_manager.execute_python("print('b')"))
        assert len(sandbox_manager.execution_history) == initial + 2

    def test_execute_shell_basic(self, sandbox_manager):
        result = asyncio.run(sandbox_manager.execute_shell("echo works"))
        assert result.exit_code == 0
        assert "works" in result.stdout

    def test_cleanup_workspace(self, sandbox_manager):
        ws = sandbox_manager.workspace
        assert ws.exists()
        sandbox_manager.cleanup()
        assert not ws.exists()
