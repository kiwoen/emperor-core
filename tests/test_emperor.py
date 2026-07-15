"""Tests for jarvis.emperor."""

from __future__ import annotations

import tempfile
from pathlib import Path

from jarvis.emperor import Emperor, EmperorConfig


# ══════════════════════════════════════════════════════════════════
# EmperorConfig
# ══════════════════════════════════════════════════════════════════


class TestEmperorConfig:
    def test_defaults(self):
        cfg = EmperorConfig()
        assert cfg.min_ministers == 3
        assert cfg.max_ministers == 20
        assert cfg.crossover_rate == 0.6
        assert cfg.api_port == 9020
        assert cfg.enable_api is False

    def test_custom(self):
        cfg = EmperorConfig(
            min_ministers=5,
            max_ministers=30,
            api_port=8080,
            enable_api=True,
        )
        assert cfg.min_ministers == 5
        assert cfg.api_port == 8080
        assert cfg.enable_api is True


# ══════════════════════════════════════════════════════════════════
# Emperor
# ══════════════════════════════════════════════════════════════════


class TestEmperorCreation:
    def test_default(self):
        emp = Emperor()
        assert emp.court is not None
        assert emp.task_engine is not None
        assert emp.court.cycle == 0

    def test_with_config(self):
        cfg = EmperorConfig(min_ministers=5, max_ministers=30)
        emp = Emperor(config=cfg)
        assert emp.config.min_ministers == 5
        assert emp.config.max_ministers == 30

    def test_status_empty(self):
        emp = Emperor()
        s = emp.status()
        assert s["version"] == "1.0"
        assert s["court"]["active_ministers"] == 0
        assert s["tasks"]["total"] == 0

    def test_dashboard_empty(self):
        emp = Emperor()
        d = emp.dashboard()
        assert "Emperor Evolution Dashboard" in d
        assert "Ministers" in d


class TestRegister:
    def test_register_single(self):
        emp = Emperor()
        emp.register("turing", domain="math", temperature=0.5)
        assert "turing" in emp.court.active_ministers

    def test_register_many(self):
        emp = Emperor()
        emp.register_many(["a", "b", "c"], domain="code")
        assert len(emp.court.active_ministers) == 3
        assert "a" in emp.court.active_ministers

    def test_register_default_name(self):
        emp = Emperor()
        emp.register("spock", domain="science")
        assert "spock" in emp.court.active_ministers


class TestEvolve:
    def test_evolve_single_cycle(self):
        emp = Emperor()
        emp.register("turing", domain="math")
        result = emp.evolve(cycles=1)
        assert "total_cycles" in result
        assert result["total_cycles"] == 1

    def test_evolve_multiple_cycles(self):
        emp = Emperor()
        emp.register("a", domain="math")
        emp.register("b", domain="math")
        result = emp.evolve(cycles=3)
        assert result["total_cycles"] == 3

    def test_evolve_zero_raises(self):
        emp = Emperor()
        try:
            emp.evolve(cycles=0)
            assert False, "should have raised"
        except ValueError:
            pass

    def test_evolve_negative_raises(self):
        emp = Emperor()
        try:
            emp.evolve(cycles=-1)
            assert False, "should have raised"
        except ValueError:
            pass


class TestExecuteTask:
    def test_execute_single(self):
        emp = Emperor()
        emp.register("turing", domain="math")
        result = emp.execute_task("What is 2+2?", domain="math")
        assert result["success"] is True
        assert "task_id" in result
        assert "minister" in result
        assert result["minister"] == "turing"

    def test_execute_with_expected(self):
        emp = Emperor()
        emp.register("alpha", domain="math")
        result = emp.execute_task("2+2", domain="math", expected="4")
        assert result["confidence"] > 0

    def test_execute_batch(self):
        emp = Emperor()
        emp.register("alpha", domain="general")
        tasks = [
            {"prompt": "hello", "domain": "general"},
            {"prompt": "world", "domain": "general"},
            {"prompt": "test", "domain": "general"},
        ]
        results = emp.execute_batch(tasks)
        assert len(results) == 3
        assert all(r["success"] for r in results)

    def test_execute_no_ministers_raises(self):
        emp = Emperor()
        try:
            emp.execute_task("test")
            assert False, "should have raised"
        except RuntimeError:
            pass

    def test_engine_summary_after_tasks(self):
        emp = Emperor()
        emp.register("alpha", domain="math")
        for i in range(5):
            emp.execute_task(f"task {i}", domain="math")
        s = emp.status()
        assert s["tasks"]["total"] == 5
        assert s["tasks"]["completed"] == 5
        assert s["tasks"]["success_rate"] > 0.9


class TestSaveAndLoad:
    def test_save_and_reload(self):
        emp = Emperor()
        emp.register("alpha", domain="math")

        with tempfile.TemporaryDirectory() as d:
            path = emp.save(path=d)
            assert Path(path).is_dir()

            emp2 = Emperor()
            emp2.load(path)
            ministers = emp2.court.active_ministers
            assert "alpha" in ministers

    def test_save_no_path_uses_data_dir(self):
        emp = Emperor()

        with tempfile.TemporaryDirectory() as d:
            emp.config.data_dir = d
            path = emp.save()
            assert Path(path).is_dir()
            assert (Path(path) / "history.json").exists()

    def test_load_nonexistent_raises(self):
        emp = Emperor()
        try:
            emp.load("/nonexistent/path/12345")
            assert False, "should have raised"
        except FileNotFoundError:
            pass

    def test_shutdown_saves(self):
        emp = Emperor()
        emp.register("alpha", domain="math")

        with tempfile.TemporaryDirectory() as d:
            emp.config.data_dir = d
            emp.shutdown()
            assert (Path(d) / "history.json").exists()


class TestApp:
    def test_app_property(self):
        emp = Emperor()
        emp.register("alpha", domain="math")
        app = emp.app
        assert app is not None
        # FastAPI app should have routes
        assert len(app.routes) > 0

    def test_app_cached(self):
        emp = Emperor()
        emp.register("alpha", domain="math")
        app1 = emp.app
        app2 = emp.app
        assert app1 is app2


class TestDashboard:
    def test_dashboard_with_ministers(self):
        emp = Emperor()
        emp.register("turing", domain="math")
        emp.register("curie", domain="science")
        d = emp.dashboard()
        assert "turing" not in d  # uses SlidingMeritReport repr
        assert "2 active" in d
        assert "Cycle" in d

    def test_dashboard_after_tasks(self):
        emp = Emperor()
        emp.register("alpha", domain="math")
        emp.execute_task("test task", domain="math")
        d = emp.dashboard()
        assert "Success" in d
        assert "Avg Merit" in d


class TestEndToEnd:
    def test_full_lifecycle(self):
        """Register → Evolve → Execute → Save → Reload → Continue."""
        emp = Emperor()
        emp.register_many(["a", "b", "c", "d"], domain="math")

        # Evolution
        r = emp.evolve(cycles=2)
        assert r["total_cycles"] == 2

        # Task execution
        for i in range(5):
            result = emp.execute_task(f"task {i}", domain="math")
            assert result["success"] is True

        # Status
        s = emp.status()
        assert s["tasks"]["total"] == 5
        assert s["tasks"]["success_rate"] > 0

        # Save and reload
        with tempfile.TemporaryDirectory() as d:
            emp.save(path=d)

            emp2 = Emperor()
            emp2.load(path=d)
            assert len(emp2.court.active_ministers) >= 1

            # Continue tasks after reload
            emp2.register("new_guy", domain="math")
            result = emp2.execute_task("after reload", domain="math")
            assert result["success"] is True
