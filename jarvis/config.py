"""Configuration system for Emperor Core.

Loads settings from jarvis.yaml (JSON-inside-YAML, compatible with Python stdlib).
Falls back to sensible defaults when no config file exists.
First run auto-generates jarvis.yaml with all defaults.

Usage:
    from jarvis.config import EmperorConfig, load_config, save_default_config

    config = load_config()                   # jarvis.yaml or defaults
    config = load_config("custom.yaml")      # custom path
    save_default_config()                    # write jarvis.yaml if missing
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DashboardConfig:
    """Frontend dashboard settings."""

    host: str = "127.0.0.1"
    port: int = 9020
    open_browser: bool = True
    refresh_interval_seconds: int = 15
    theme: str = "dark"


@dataclass
class SchedulerConfig:
    """Background scheduler settings."""

    auto_schedule: bool = True
    evolve_interval_minutes: float = 5.0
    task_interval_minutes: float = 3.0
    task_batch_size: int = 5


@dataclass
class EvolutionConfig:
    """Evolution / breeding / auto-tune thresholds."""

    merit_delta_range: tuple = (-2, 2)
    stability_delta_range: tuple = (-0.02, 0.02)
    streak_bonus_threshold: int = 5
    high_hit_rate_threshold: float = 0.5


@dataclass
class CapabilityConfig:
    """Capability registration settings."""

    enabled_capabilities: list = field(default_factory=lambda: [
        "datetime", "math", "random", "text", "file_info",
        "hash", "json_tool", "uuid_gen", "web_search", "web_fetch",
    ])
    web_search_timeout: int = 10
    web_fetch_timeout: int = 10
    web_fetch_max_chars: int = 2000


@dataclass
class DatabaseConfig:
    """SQLite persistence settings."""

    db_path: str = "jarvis.db"
    wal_mode: bool = True
    max_history_rows: int = 10000


@dataclass
class EmperorConfig:
    """Top-level configuration aggregator."""

    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    evolution: EvolutionConfig = field(default_factory=EvolutionConfig)
    capability: CapabilityConfig = field(default_factory=CapabilityConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    seed_ministers: list = field(default_factory=lambda: [
        {"name": "turing", "domain": "general"},
        {"name": "curie", "domain": "science"},
        {"name": "hinton", "domain": "data"},
        {"name": "bengio", "domain": "data"},
        {"name": "lecun", "domain": "code"},
        {"name": "goodfellow", "domain": "math"},
        {"name": "sutton", "domain": "general"},
        {"name": "silver", "domain": "general"},
    ])
    max_ministers: int = 50


# ══════════════════════════════════════════════════════════════════
# Serialization helpers
# ══════════════════════════════════════════════════════════════════


def _config_to_dict(config: EmperorConfig) -> dict:
    """Convert config object to JSON-serializable dict."""
    return {
        "dashboard": {
            "host": config.dashboard.host,
            "port": config.dashboard.port,
            "open_browser": config.dashboard.open_browser,
            "refresh_interval_seconds": config.dashboard.refresh_interval_seconds,
            "theme": config.dashboard.theme,
        },
        "scheduler": {
            "auto_schedule": config.scheduler.auto_schedule,
            "evolve_interval_minutes": config.scheduler.evolve_interval_minutes,
            "task_interval_minutes": config.scheduler.task_interval_minutes,
            "task_batch_size": config.scheduler.task_batch_size,
        },
        "evolution": {
            "merit_delta_range": list(config.evolution.merit_delta_range),
            "stability_delta_range": list(config.evolution.stability_delta_range),
            "streak_bonus_threshold": config.evolution.streak_bonus_threshold,
            "high_hit_rate_threshold": config.evolution.high_hit_rate_threshold,
        },
        "capability": {
            "enabled_capabilities": config.capability.enabled_capabilities,
            "web_search_timeout": config.capability.web_search_timeout,
            "web_fetch_timeout": config.capability.web_fetch_timeout,
            "web_fetch_max_chars": config.capability.web_fetch_max_chars,
        },
        "database": {
            "db_path": config.database.db_path,
            "wal_mode": config.database.wal_mode,
            "max_history_rows": config.database.max_history_rows,
        },
        "seed_ministers": config.seed_ministers,
        "max_ministers": config.max_ministers,
    }


def _apply_raw_config(config: EmperorConfig, raw: dict) -> None:
    """Apply raw dict values onto a config object, only overriding present keys."""
    if "dashboard" in raw:
        d = raw["dashboard"]
        if "host" in d:
            config.dashboard.host = d["host"]
        if "port" in d:
            config.dashboard.port = d["port"]
        if "open_browser" in d:
            config.dashboard.open_browser = d["open_browser"]
        if "refresh_interval_seconds" in d:
            config.dashboard.refresh_interval_seconds = d["refresh_interval_seconds"]
        if "theme" in d:
            config.dashboard.theme = d["theme"]

    if "scheduler" in raw:
        s = raw["scheduler"]
        if "auto_schedule" in s:
            config.scheduler.auto_schedule = s["auto_schedule"]
        if "evolve_interval_minutes" in s:
            config.scheduler.evolve_interval_minutes = s["evolve_interval_minutes"]
        if "task_interval_minutes" in s:
            config.scheduler.task_interval_minutes = s["task_interval_minutes"]
        if "task_batch_size" in s:
            config.scheduler.task_batch_size = s["task_batch_size"]

    if "evolution" in raw:
        e = raw["evolution"]
        if "merit_delta_range" in e:
            config.evolution.merit_delta_range = tuple(e["merit_delta_range"])
        if "stability_delta_range" in e:
            config.evolution.stability_delta_range = tuple(e["stability_delta_range"])
        if "streak_bonus_threshold" in e:
            config.evolution.streak_bonus_threshold = e["streak_bonus_threshold"]
        if "high_hit_rate_threshold" in e:
            config.evolution.high_hit_rate_threshold = e["high_hit_rate_threshold"]

    if "capability" in raw:
        c = raw["capability"]
        if "enabled_capabilities" in c:
            config.capability.enabled_capabilities = c["enabled_capabilities"]
        if "web_search_timeout" in c:
            config.capability.web_search_timeout = c["web_search_timeout"]
        if "web_fetch_timeout" in c:
            config.capability.web_fetch_timeout = c["web_fetch_timeout"]
        if "web_fetch_max_chars" in c:
            config.capability.web_fetch_max_chars = c["web_fetch_max_chars"]

    if "database" in raw:
        db = raw["database"]
        if "db_path" in db:
            config.database.db_path = db["db_path"]
        if "wal_mode" in db:
            config.database.wal_mode = db["wal_mode"]
        if "max_history_rows" in db:
            config.database.max_history_rows = db["max_history_rows"]

    if "seed_ministers" in raw:
        config.seed_ministers = raw["seed_ministers"]
    if "max_ministers" in raw:
        config.max_ministers = raw["max_ministers"]


# ══════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════


def load_config(config_path: str = "jarvis.yaml") -> EmperorConfig:
    """Load config from a JSON/YAML file, falling back to defaults.

    Args:
        config_path: Path to the config file (JSON inside YAML).

    Returns:
        EmperorConfig with merged values.
    """
    config = EmperorConfig()

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _apply_raw_config(config, raw)

    return config


def save_default_config(config_path: str = "jarvis.yaml") -> bool:
    """Write default config to disk if the file does not already exist.

    Returns True if a new file was created, False if it already existed.
    """
    if os.path.exists(config_path):
        return False

    config = EmperorConfig()
    raw = _config_to_dict(config)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
    return True
