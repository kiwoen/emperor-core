"""Tests for the YAML config system (jarvis/config.py)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from jarvis.config import (
    EmperorConfig,
    DashboardConfig,
    SchedulerConfig,
    EvolutionConfig,
    CapabilityConfig,
    DatabaseConfig,
    load_config,
    save_default_config,
    _config_to_dict,
    _apply_raw_config,
)


# ══════════════════════════════════════════════════════════════════
# Default config
# ══════════════════════════════════════════════════════════════════


class TestDefaultConfig:
    def test_default_config_creates_valid_object(self):
        cfg = EmperorConfig()
        assert isinstance(cfg.dashboard, DashboardConfig)
        assert isinstance(cfg.scheduler, SchedulerConfig)
        assert isinstance(cfg.evolution, EvolutionConfig)
        assert isinstance(cfg.capability, CapabilityConfig)
        assert isinstance(cfg.database, DatabaseConfig)

    def test_default_dashboard_values(self):
        cfg = EmperorConfig()
        assert cfg.dashboard.host == "127.0.0.1"
        assert cfg.dashboard.port == 9020
        assert cfg.dashboard.open_browser is True
        assert cfg.dashboard.refresh_interval_seconds == 15
        assert cfg.dashboard.theme == "dark"

    def test_default_scheduler_values(self):
        cfg = EmperorConfig()
        assert cfg.scheduler.auto_schedule is True
        assert cfg.scheduler.evolve_interval_minutes == 5
        assert cfg.scheduler.task_interval_minutes == 3
        assert cfg.scheduler.task_batch_size == 5

    def test_default_evolution_values(self):
        cfg = EmperorConfig()
        assert cfg.evolution.merit_delta_range == (-2, 2)
        assert cfg.evolution.stability_delta_range == (-0.02, 0.02)
        assert cfg.evolution.streak_bonus_threshold == 5
        assert cfg.evolution.high_hit_rate_threshold == 0.5

    def test_default_capability_values(self):
        cfg = EmperorConfig()
        assert "datetime" in cfg.capability.enabled_capabilities
        assert "math" in cfg.capability.enabled_capabilities
        assert "web_search" in cfg.capability.enabled_capabilities
        assert cfg.capability.web_search_timeout == 10
        assert cfg.capability.web_fetch_timeout == 10
        assert cfg.capability.web_fetch_max_chars == 2000

    def test_default_database_values(self):
        cfg = EmperorConfig()
        assert cfg.database.db_path == "jarvis.db"
        assert cfg.database.wal_mode is True
        assert cfg.database.max_history_rows == 10000

    def test_default_seed_ministers_count(self):
        cfg = EmperorConfig()
        assert len(cfg.seed_ministers) == 8

    def test_default_max_ministers(self):
        cfg = EmperorConfig()
        assert cfg.max_ministers == 50


# ══════════════════════════════════════════════════════════════════
# File I/O
# ══════════════════════════════════════════════════════════════════


class TestConfigFileIO:
    def test_load_missing_file_returns_defaults(self):
        cfg = load_config("__nonexistent_config_xyz__.yaml")
        assert cfg.dashboard.port == 9020

    def test_save_and_load_roundtrip(self):
        tmp = tempfile.mktemp(suffix=".yaml")
        try:
            cfg = EmperorConfig()
            cfg.dashboard.port = 8888
            raw = _config_to_dict(cfg)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f)

            loaded = load_config(tmp)
            assert loaded.dashboard.port == 8888
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_save_default_config_writes_file(self):
        tmp = tempfile.mktemp(suffix=".yaml")
        try:
            result = save_default_config(tmp)
            assert result is True
            assert os.path.exists(tmp)
            with open(tmp, "r", encoding="utf-8") as f:
                raw = json.load(f)
            assert "dashboard" in raw
            assert "scheduler" in raw
            assert "evolution" in raw
            assert "capability" in raw
            assert "database" in raw
            assert "seed_ministers" in raw
            assert "max_ministers" in raw
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_save_default_config_no_overwrite(self):
        tmp = tempfile.mktemp(suffix=".yaml")
        try:
            save_default_config(tmp)
            result = save_default_config(tmp)
            assert result is False  # already exists
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ══════════════════════════════════════════════════════════════════
# Partial override
# ══════════════════════════════════════════════════════════════════


class TestPartialOverride:
    def test_dashboard_override(self):
        cfg = EmperorConfig()
        _apply_raw_config(cfg, {"dashboard": {"port": 7777, "theme": "light"}})
        assert cfg.dashboard.port == 7777
        assert cfg.dashboard.theme == "light"
        assert cfg.dashboard.host == "127.0.0.1"  # untouched

    def test_scheduler_override(self):
        cfg = EmperorConfig()
        _apply_raw_config(cfg, {"scheduler": {"auto_schedule": False}})
        assert cfg.scheduler.auto_schedule is False
        assert cfg.scheduler.evolve_interval_minutes == 5  # untouched

    def test_evolution_override(self):
        cfg = EmperorConfig()
        _apply_raw_config(cfg, {"evolution": {"merit_delta_range": [-5, 5]}})
        assert cfg.evolution.merit_delta_range == (-5, 5)

    def test_capability_override(self):
        cfg = EmperorConfig()
        _apply_raw_config(cfg, {"capability": {"enabled_capabilities": ["math"]}})
        assert cfg.capability.enabled_capabilities == ["math"]

    def test_database_override(self):
        cfg = EmperorConfig()
        _apply_raw_config(cfg, {"database": {"db_path": "prod.db"}})
        assert cfg.database.db_path == "prod.db"

    def test_seed_ministers_override(self):
        cfg = EmperorConfig()
        custom = [{"name": "alice", "domain": "math"}]
        _apply_raw_config(cfg, {"seed_ministers": custom})
        assert cfg.seed_ministers == custom

    def test_max_ministers_override(self):
        cfg = EmperorConfig()
        _apply_raw_config(cfg, {"max_ministers": 20})
        assert cfg.max_ministers == 20


# ══════════════════════════════════════════════════════════════════
# Round-trip
# ══════════════════════════════════════════════════════════════════


class TestConfigRoundTrip:
    def test_dict_to_config_roundtrip(self):
        original = EmperorConfig()
        original.dashboard.port = 1234
        original.scheduler.evolve_interval_minutes = 10

        raw = _config_to_dict(original)
        loaded = EmperorConfig()
        _apply_raw_config(loaded, raw)

        assert loaded.dashboard.port == 1234
        assert loaded.scheduler.evolve_interval_minutes == 10
        assert loaded.dashboard.host == original.dashboard.host
        assert loaded.max_ministers == original.max_ministers

    def test_full_save_load_roundtrip(self):
        tmp = tempfile.mktemp(suffix=".yaml")
        try:
            cfg = EmperorConfig()
            cfg.dashboard.theme = "light"
            cfg.scheduler.auto_schedule = False
            cfg.evolution.streak_bonus_threshold = 3
            cfg.capability.web_search_timeout = 30
            cfg.database.max_history_rows = 5000
            cfg.seed_ministers = [{"name": "bob", "domain": "general"}]
            cfg.max_ministers = 10

            raw = _config_to_dict(cfg)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)

            loaded = load_config(tmp)
            assert loaded.dashboard.theme == "light"
            assert loaded.scheduler.auto_schedule is False
            assert loaded.evolution.streak_bonus_threshold == 3
            assert loaded.capability.web_search_timeout == 30
            assert loaded.database.max_history_rows == 5000
            assert loaded.seed_ministers == [{"name": "bob", "domain": "general"}]
            assert loaded.max_ministers == 10
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ══════════════════════════════════════════════════════════════════
# Dependency check
# ══════════════════════════════════════════════════════════════════


class TestNoPyYAML:
    def test_no_yaml_import_in_config_module(self):
        """Verify that config.py does not import PyYAML (uses stdlib json only)."""
        import jarvis.config as cfg_mod
        source = cfg_mod.__file__
        assert source is not None
        with open(source, "r", encoding="utf-8") as f:
            content = f.read()

        # Check no 'import yaml' or 'from yaml' in the module
        assert "import yaml" not in content
        assert "from yaml" not in content
