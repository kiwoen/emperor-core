"""Tests for Emperor auto-start (one-command live dashboard)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

# Pre-import uvicorn so that patches on uvicorn.run work when the
# serve() method does `import uvicorn` (which is then a no-op).
import uvicorn  # noqa: F401

from jarvis.emperor import Emperor, EmperorConfig


# ══════════════════════════════════════════════════════════════════
# Auto-seed ministers
# ══════════════════════════════════════════════════════════════════


class TestAutoSeedMinisters:
    def test_ensure_default_ministers_empty_court(self):
        emp = Emperor()
        assert len(emp.court.active_ministers) == 0
        seeded = emp._ensure_default_ministers()
        assert seeded > 0
        assert len(emp.court.active_ministers) == seeded
        seeded_names = set(emp.court.active_ministers)
        assert "turing" in seeded_names
        assert "curie" in seeded_names
        assert "hinton" in seeded_names

    def test_ensure_default_ministers_partially_populated(self):
        """If some ministers already exist, duplicates are skipped."""
        emp = Emperor()
        # Pre-register "turing" which is in DEFAULT_MINISTERS
        emp.register("turing", domain="math")
        assert len(emp.court.active_ministers) == 1
        seeded = emp._ensure_default_ministers()
        # "turing" skipped; 7 remaining defaults registered
        assert seeded == 7
        assert len(emp.court.active_ministers) == 8

    def test_ensure_default_ministers_all_present(self):
        """If all defaults are already registered, seed is a no-op."""
        emp = Emperor()
        for name, domain in emp.DEFAULT_MINISTERS:
            emp.register(name, domain=domain)
        assert len(emp.court.active_ministers) == len(emp.DEFAULT_MINISTERS)
        seeded = emp._ensure_default_ministers()
        assert seeded == 0

    def test_ensure_default_ministers_respects_max_ministers(self):
        cfg = EmperorConfig(max_ministers=3)
        emp = Emperor(config=cfg)
        seeded = emp._ensure_default_ministers()
        assert seeded == 3
        assert len(emp.court.active_ministers) == 3

    def test_default_ministers_list(self):
        emp = Emperor()
        assert hasattr(emp, "DEFAULT_MINISTERS")
        assert isinstance(emp.DEFAULT_MINISTERS, list)
        assert len(emp.DEFAULT_MINISTERS) > 0
        for name, domain in emp.DEFAULT_MINISTERS:
            assert isinstance(name, str)
            assert isinstance(domain, str)


# ══════════════════════════════════════════════════════════════════
# Auto-start scheduler
# ══════════════════════════════════════════════════════════════════


class TestAutoStartScheduler:
    def test_auto_start_scheduler_empty_court(self):
        emp = Emperor()
        started = emp._auto_start_scheduler()
        assert started is False
        assert emp.scheduler.state.name == "IDLE"

    def test_auto_start_scheduler_with_ministers(self):
        emp = Emperor()
        emp.register("turing", domain="math")
        started = emp._auto_start_scheduler()
        assert started is True
        assert emp.scheduler.state.name == "RUNNING"
        report = emp.scheduler.report()
        assert len(report.entries) >= 1

    def test_auto_start_scheduler_already_running(self):
        emp = Emperor()
        emp.register("turing", domain="math")
        started1 = emp._auto_start_scheduler()
        assert started1 is True
        started2 = emp._auto_start_scheduler()
        assert started2 is False

    def test_scheduler_jobs_created(self):
        emp = Emperor()
        emp.register("turing", domain="math")
        emp._auto_start_scheduler()
        report = emp.scheduler.report()
        # entries are dicts with "name" key
        names = [e["name"] for e in report.entries]
        assert any("evolution" in n for n in names)
        assert any("task" in n for n in names)


# ══════════════════════════════════════════════════════════════════
# Serve() integration
# ══════════════════════════════════════════════════════════════════


class TestServeAutoStart:
    @pytest.fixture(autouse=True)
    def _patch_uvicorn(self):
        """Patch uvicorn.run + create_app globally to avoid server spin-up."""
        from jarvis import court_api
        with patch("uvicorn.run"), \
             patch.object(court_api, "create_app") as mock_create:
            mock_create.return_value.extra = {}
            yield

    def test_serve_with_auto_seed(self):
        cfg = EmperorConfig(auto_seed_ministers=True, auto_schedule=False)
        emp = Emperor(config=cfg)
        assert len(emp.court.active_ministers) == 0
        emp.serve(port=9999)
        assert len(emp.court.active_ministers) > 0
        assert emp.scheduler.state.name == "IDLE"

    def test_serve_with_auto_schedule(self):
        cfg = EmperorConfig(auto_seed_ministers=True, auto_schedule=True)
        emp = Emperor(config=cfg)
        emp.serve(port=9999)
        assert len(emp.court.active_ministers) > 0
        assert emp.scheduler.state.name == "RUNNING"

    def test_serve_without_auto_features(self):
        cfg = EmperorConfig(auto_seed_ministers=False, auto_schedule=False)
        emp = Emperor(config=cfg)
        emp.serve(port=9999)
        assert len(emp.court.active_ministers) == 0
        assert emp.scheduler.state.name == "IDLE"

    def test_serve_custom_intervals(self):
        cfg = EmperorConfig(
            auto_seed_ministers=True,
            auto_schedule=True,
            auto_evolve_interval_minutes=10.0,
            auto_evolve_cycles=2,
            auto_tasks_interval_minutes=7.5,
        )
        emp = Emperor(config=cfg)
        emp.serve(port=9999)
        assert emp.scheduler.state.name == "RUNNING"
        assert emp.config.auto_evolve_interval_minutes == 10.0
        assert emp.config.auto_evolve_cycles == 2
        assert emp.config.auto_tasks_interval_minutes == 7.5


# ══════════════════════════════════════════════════════════════════
# End-to-end: one-command live dashboard
# ══════════════════════════════════════════════════════════════════


def test_one_command_live_dashboard():
    """End-to-end: Emperor().serve() → seeded ministers + running scheduler."""
    cfg = EmperorConfig(
        auto_seed_ministers=True,
        auto_schedule=True,
        auto_evolve_interval_minutes=0.1,
        auto_tasks_interval_minutes=0.1,
    )
    emp = Emperor(config=cfg)

    from jarvis import court_api as ca
    with patch("uvicorn.run"), patch.object(ca, "create_app") as mock_app:
        mock_app.return_value.extra = {}
        emp.serve(port=9999)

    assert len(emp.court.active_ministers) >= 3
    assert emp.scheduler.state.name == "RUNNING"
    report = emp.scheduler.report()
    assert len(report.entries) >= 2

    status = emp.status()
    assert status["court"]["active_ministers"] > 0
    assert status["tasks"]["success_rate"] >= 0.0

    emp.shutdown()
    assert emp.scheduler.state.name == "STOPPED"