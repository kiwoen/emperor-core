"""Tests for Emperor CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from jarvis.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def tmp_court(tmp_path):
    """Create a temp court directory and init it."""
    d = tmp_path / "court"
    d.mkdir()
    return d


# ══════════════════════════════════════════════════════════════════
# init
# ══════════════════════════════════════════════════════════════════

class TestCLIInit:
    def test_init_creates_directory_and_files(self, runner, tmp_path):
        court_dir = tmp_path / "court"
        result = runner.invoke(cli, ["init", "--path", str(court_dir)])
        assert result.exit_code == 0
        assert (court_dir / "config.yaml").exists()

    def test_init_idempotent(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        result = runner.invoke(cli, ["init", "--path", str(tmp_court)])
        assert result.exit_code == 0


# ══════════════════════════════════════════════════════════════════
# status
# ══════════════════════════════════════════════════════════════════

class TestCLIStatus:
    def test_status_on_fresh_court(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        result = runner.invoke(cli, ["status", "--path", str(tmp_court)])
        assert result.exit_code == 0

    def test_status_json(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        result = runner.invoke(
            cli, ["status", "--path", str(tmp_court), "--fmt", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)


# ══════════════════════════════════════════════════════════════════
# register
# ══════════════════════════════════════════════════════════════════

class TestCLIRegister:
    def test_register_new_minister(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        result = runner.invoke(
            cli,
            [
                "register", "--path", str(tmp_court),
                "--name", "turing", "--domain", "math",
            ],
        )
        assert result.exit_code == 0
        assert "Registered" in result.output

    def test_register_auto_name(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        result = runner.invoke(
            cli, ["register", "--path", str(tmp_court), "--domain", "code"]
        )
        assert result.exit_code == 0
        assert "Registered" in result.output


# ══════════════════════════════════════════════════════════════════
# evolve
# ══════════════════════════════════════════════════════════════════

class TestCLIEvolve:
    def test_evolve_no_ministers_rejected(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        result = runner.invoke(
            cli, ["evolve", "--path", str(tmp_court), "--cycles", "1"]
        )
        assert result.exit_code != 0

    def test_evolve_single_cycle(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        runner.invoke(
            cli,
            ["register", "--path", str(tmp_court),
             "--name", "turing", "--domain", "math"],
        )
        result = runner.invoke(
            cli, ["evolve", "--path", str(tmp_court), "--cycles", "1"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total_cycles" in data

    def test_evolve_multiple_cycles(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        runner.invoke(
            cli,
            ["register", "--path", str(tmp_court),
             "--name", "turing", "--domain", "math"],
        )
        result = runner.invoke(
            cli, ["evolve", "--path", str(tmp_court), "--cycles", "3"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_cycles"] == 3


# ══════════════════════════════════════════════════════════════════
# list
# ══════════════════════════════════════════════════════════════════

class TestCLIList:
    def test_list_after_register(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        runner.invoke(
            cli,
            ["register", "--path", str(tmp_court),
             "--name", "turing", "--domain", "math"],
        )
        result = runner.invoke(cli, ["list", "--path", str(tmp_court)])
        assert result.exit_code == 0
        assert "turing" in result.output


# ══════════════════════════════════════════════════════════════════
# history
# ══════════════════════════════════════════════════════════════════

class TestCLIHistory:
    def test_history_empty(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        result = runner.invoke(cli, ["history", "--path", str(tmp_court)])
        assert result.exit_code == 0
        assert "No evolution history" in result.output

    def test_history_after_evolve(self, runner, tmp_court):
        runner.invoke(cli, ["init", "--path", str(tmp_court)])
        runner.invoke(
            cli,
            ["register", "--path", str(tmp_court),
             "--name", "turing", "--domain", "math"],
        )
        runner.invoke(
            cli, ["evolve", "--path", str(tmp_court), "--cycles", "2"]
        )
        result = runner.invoke(cli, ["history", "--path", str(tmp_court)])
        assert result.exit_code == 0
        assert "Cycle" in result.output


# ══════════════════════════════════════════════════════════════════
# config
# ══════════════════════════════════════════════════════════════════

class TestCLIConfig:
    def test_config_init_creates_file(self, runner, tmp_path):
        court_dir = tmp_path / "court"
        result = runner.invoke(
            cli, ["config", "init", "--path", str(court_dir)]
        )
        assert result.exit_code == 0
        assert (court_dir / "config.yaml").exists()

    def test_config_show_existing(self, runner, tmp_court):
        runner.invoke(cli, ["config", "init", "--path", str(tmp_court)])
        result = runner.invoke(
            cli, ["config", "show", "--path", str(tmp_court)]
        )
        assert result.exit_code == 0
        assert "elitism_count" in result.output

    def test_config_show_missing(self, runner, tmp_court):
        result = runner.invoke(
            cli, ["config", "show", "--path", str(tmp_court)]
        )
        assert result.exit_code == 0
        assert "No config" in result.output
