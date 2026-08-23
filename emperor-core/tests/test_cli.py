"""Tests for Emperor CLI (argparse-based)."""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock, patch

import pytest

from jarvis.cli import main, VERSION


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════


def run_cli(argv: list[str]) -> tuple[int, str]:
    """Run main() with given argv, return (exit_code, stdout)."""
    old_argv = sys.argv
    old_stdout = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        sys.argv = ["jarvis"] + argv
        try:
            main()
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
    return exit_code, buf.getvalue()


# ══════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════


class TestCLINoArgs:
    """Tests for no-args and --version."""

    def test_no_args_shows_help(self):
        exit_code, output = run_cli([])
        assert exit_code == 1
        assert "jarvis" in output.lower() or "usage" in output.lower()

    def test_version(self):
        exit_code, output = run_cli(["--version"])
        assert exit_code == 0
        assert VERSION in output

    def test_invalid_command(self):
        exit_code, output = run_cli(["nosuchcommand"])
        assert exit_code != 0


class TestCLIStatus:
    """Tests for status command."""

    @patch("jarvis.emperor.Emperor")
    def test_status_command(self, mock_emperor_cls):
        mock_emperor = MagicMock()
        mock_court = MagicMock()
        mock_snap = MagicMock()
        mock_snap.total_ministers = 5
        mock_snap.active_count = 5
        mock_snap.ministers = [
            MagicMock(domain="math"),
            MagicMock(domain="code"),
            MagicMock(domain="general"),
        ]
        mock_court.inspect.snapshot.return_value = mock_snap
        mock_court.cycle = 3
        mock_court.success_rate = 0.85
        mock_ranking = MagicMock()
        mock_ranking.merit_score = 95.0
        mock_ranking.minister = "turing"
        mock_court.merit_ranking = [mock_ranking]
        mock_emperor.court = mock_court
        mock_emperor._scheduler = None
        mock_emperor_cls.return_value = mock_emperor

        exit_code, output = run_cli(["status"])
        assert exit_code == 0
        assert "Emperor Core" in output
        assert "5" in output
        assert "turing" in output


class TestCLIMinisters:
    """Tests for ministers command."""

    @patch("jarvis.emperor.Emperor")
    def test_ministers_empty(self, mock_emperor_cls):
        mock_emperor = MagicMock()
        mock_court = MagicMock()
        mock_snap = MagicMock()
        mock_snap.ministers = []
        mock_court.inspect.snapshot.return_value = mock_snap
        mock_emperor.court = mock_court
        mock_emperor_cls.return_value = mock_emperor

        exit_code, output = run_cli(["ministers"])
        assert exit_code == 0
        assert "暂无大臣" in output

    @patch("jarvis.emperor.Emperor")
    def test_ministers_with_data(self, mock_emperor_cls):
        mock_emperor = MagicMock()
        mock_court = MagicMock()
        mock_snap = MagicMock()

        m1 = MagicMock()
        m1.name = "turing"
        m1.domain = "math"
        m1.merit = 80.0

        m2 = MagicMock()
        m2.name = "curie"
        m2.domain = "science"
        m2.merit = 60.0

        mock_snap.ministers = [m1, m2]
        mock_court.inspect.snapshot.return_value = mock_snap
        mock_court._sm._genomes = {}
        mock_ranking = [
            MagicMock(minister="turing", merit_score=80.0),
            MagicMock(minister="curie", merit_score=60.0),
        ]
        mock_court.merit_ranking = mock_ranking
        mock_emperor.court = mock_court
        mock_emperor_cls.return_value = mock_emperor

        exit_code, output = run_cli(["ministers"])
        assert exit_code == 0
        assert "turing" in output
        assert "curie" in output
        assert "math" in output
        assert "science" in output


class TestCLITask:
    """Tests for task command."""

    @patch("jarvis.emperor.Emperor")
    def test_task_command(self, mock_emperor_cls):
        mock_emperor = MagicMock()
        mock_emperor.execute_task.return_value = {
            "task_id": "abc12345",
            "minister": "turing",
            "success": True,
            "confidence": 0.95,
            "execution_time_ms": 150.0,
            "response": "Result: 5\n",
            "error": "",
        }
        mock_emperor_cls.return_value = mock_emperor

        exit_code, output = run_cli(["task", "计算 2+3"])
        assert exit_code == 0
        assert "turing" in output
        assert "Result: 5" in output
        assert "abc12345" in output

    @patch("jarvis.emperor.Emperor")
    def test_task_with_domain(self, mock_emperor_cls):
        mock_emperor = MagicMock()
        mock_emperor.execute_task.return_value = {
            "task_id": "xyz",
            "minister": "lovelace",
            "success": True,
            "confidence": 0.88,
            "execution_time_ms": 200.0,
            "response": "4",
            "error": "",
        }
        mock_emperor_cls.return_value = mock_emperor

        exit_code, output = run_cli(["task", "--domain", "math", "2+2"])
        assert exit_code == 0
        mock_emperor.execute_task.assert_called_with("2+2", domain="math")

    @patch("jarvis.emperor.Emperor")
    def test_task_failure(self, mock_emperor_cls):
        mock_emperor = MagicMock()
        mock_emperor.execute_task.return_value = {
            "task_id": "fail01",
            "minister": "unknown",
            "success": False,
            "confidence": 0.1,
            "execution_time_ms": 500.0,
            "response": "",
            "error": "No capable minister found",
        }
        mock_emperor_cls.return_value = mock_emperor

        exit_code, output = run_cli(["task", "impossible query"])
        assert exit_code == 0
        assert "失败" in output or "error" in output.lower()


class TestCLIEvolve:
    """Tests for evolve command."""

    @patch("jarvis.emperor.Emperor")
    def test_evolve_no_ministers(self, mock_emperor_cls):
        mock_emperor = MagicMock()
        mock_court = MagicMock()
        mock_court.active_ministers = []
        mock_emperor.court = mock_court
        mock_emperor_cls.return_value = mock_emperor

        exit_code, output = run_cli(["evolve"])
        assert exit_code == 0
        assert "无活跃大臣" in output

    @patch("jarvis.emperor.Emperor")
    def test_evolve_command(self, mock_emperor_cls):
        mock_emperor = MagicMock()
        mock_court = MagicMock()
        mock_court.active_ministers = ["turing", "curie"]
        mock_court.evolve.return_value = {
            "active_count": 2,
            "eliminated_count": 0,
            "new_spawns": 1,
        }
        mock_emperor.court = mock_court
        mock_emperor_cls.return_value = mock_emperor

        exit_code, output = run_cli(["evolve", "--cycles", "2"])
        assert exit_code == 0
        mock_court.evolve.assert_called_with(2)
        assert "进化完成" in output


class TestCLIAlerts:
    """Tests for alerts command."""

    @patch("jarvis.emperor.Emperor")
    def test_alerts_empty(self, mock_emperor_cls):
        mock_emperor = MagicMock()
        mock_alerts = MagicMock()
        mock_alerts.history.return_value = []
        mock_emperor.alerts = mock_alerts
        mock_emperor_cls.return_value = mock_emperor

        exit_code, output = run_cli(["alerts"])
        assert exit_code == 0
        assert "无活跃告警" in output


class TestCLIServe:
    """Tests for serve help output."""

    def test_serve_help(self):
        exit_code, output = run_cli(["serve", "--help"])
        assert exit_code == 0
        assert "Dashboard" in output or "port" in output.lower()
