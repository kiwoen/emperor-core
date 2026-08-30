"""Configuration system for 幻炘AI (huanxin).

This module is the **single source of truth** for all runtime configuration.

The previous codebase maintained three separate ``HuanxinConfig`` definitions
(``huanxin/config.py``, ``huanxin/core/__init__.py`` and
``huanxin/core/config.py``). They have been consolidated here into one
pydantic-settings model: :class:`HuanxinConfig`.

Design notes
------------
* ``HuanxinConfig`` is a pydantic ``BaseSettings`` with ``env_prefix="HUANXIN_"``
  and ``env_file=".env"``. Every field can therefore be overridden by an
  environment variable (nested sub-models use ``__`` as delimiter, e.g.
  ``HUANXIN_SANDBOX__ENGINE=docker``).
* ``extra="allow"`` keeps the model forgiving when unknown keys appear.
* ``load_config(path)`` provides a backward-compatible loader for the legacy
  ``huanxin.yaml`` file (JSON-inside-YAML compatible). Keys are matched
  case-insensitively and missing keys fall back to defaults.
* ``save_default_config(path)`` writes a complete default config to disk when
  the file does not already exist (first-run behaviour preserved).

Sub-models
----------
``DashboardConfig``, ``SchedulerConfig``, ``EvolutionConfig``,
``CapabilityConfig``, ``DatabaseConfig`` (legacy dashboard/scheduler schema)
plus ``ModelConfig``, ``MemoryConfig`` and ``SandboxConfig`` (runtime engine
schema) are all embedded directly.

Usage
-----
    from huanxin.config import HuanxinConfig, load_config, save_default_config

    config = load_config()                   # huanxin.yaml or defaults
    config = load_config("custom.yaml")      # custom path
    save_default_config()                    # write huanxin.yaml if missing
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger("huanxin.config")


# ══════════════════════════════════════════════════════════════════
# Sub-models (legacy dashboard / scheduler schema)
# ══════════════════════════════════════════════════════════════════


class DashboardConfig(BaseModel):
    """Frontend dashboard settings."""

    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True
    refresh_interval_seconds: int = 15
    theme: str = "dark"
    weather_city: str = "北京"


class SchedulerConfig(BaseModel):
    """Background scheduler settings."""

    auto_schedule: bool = True
    evolve_interval_minutes: float = 5.0
    task_interval_minutes: float = 3.0
    task_batch_size: int = 5


class EvolutionConfig(BaseModel):
    """Evolution / breeding / auto-tune thresholds (merit-based)."""

    merit_delta_range: tuple = (-2, 2)
    stability_delta_range: tuple = (-0.02, 0.02)
    streak_bonus_threshold: int = 5
    high_hit_rate_threshold: float = 0.5


class CapabilityConfig(BaseModel):
    """Capability registration settings."""

    enabled_capabilities: list = [
        "datetime", "math", "random", "text", "file_info",
        "hash", "json_tool", "uuid_gen",
        "weather", "news", "web_search", "web_fetch",
    ]
    web_search_timeout: int = 10
    web_fetch_timeout: int = 10
    web_fetch_max_chars: int = 2000


class DatabaseConfig(BaseModel):
    """SQLite persistence settings."""

    db_path: str = "huanxin.db"
    wal_mode: bool = True
    max_history_rows: int = 10000


# ══════════════════════════════════════════════════════════════════
# Sub-models (runtime engine schema)
# ══════════════════════════════════════════════════════════════════


class ModelConfig(BaseModel):
    """LLM model configuration."""

    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    top_p: float = 1.0

    # Model routing: which model for which task type.
    task_model_map: dict = {
        "code_generation": "gpt-4o",
        "research": "gpt-4o-mini",
        "creative_writing": "claude-3-5-sonnet",
        "translation": "gpt-4o-mini",
        "vision": "gpt-4o",
        "embedding": "text-embedding-3-small",
    }


class MemoryConfig(BaseModel):
    """Vector memory configuration."""

    engine: str = "chromadb"
    persist_dir: str = "./data/memory"
    embedding_model: str = "text-embedding-3-small"
    max_context_length: int = 100000
    retrieval_top_k: int = 20
    auto_compress_threshold: int = 1000  # conversations after which to compress


class SandboxConfig(BaseModel):
    """Code execution sandbox configuration."""

    engine: str = "docker"  # docker / podman / local_subprocess / local_direct
    image: str = "huanxin-sandbox:latest"
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    timeout_seconds: int = 300
    network_enabled: bool = False
    allowed_paths: list = []


# ══════════════════════════════════════════════════════════════════
# Master configuration
# ══════════════════════════════════════════════════════════════════

DEFAULT_SEED_MINISTERS: list = [
    {"name": "turing", "domain": "general"},
    {"name": "curie", "domain": "science"},
    {"name": "hinton", "domain": "data"},
    {"name": "bengio", "domain": "data"},
    {"name": "lecun", "domain": "code"},
    {"name": "goodfellow", "domain": "math"},
    {"name": "sutton", "domain": "general"},
    {"name": "silver", "domain": "general"},
]

DEFAULT_DOMAINS_ENABLED: list = [
    "personal", "research", "engineering", "creator",
    "security", "health", "finance", "home",
]


class HuanxinConfig(BaseSettings):
    """Top-level, single-source-of-truth configuration for 幻炘AI.

    Every field can be overridden by an environment variable prefixed with
    ``HUANXIN_`` (nested sub-models use ``__`` as a delimiter). Unknown keys
    are tolerated (``extra="allow"``).
    """

    model_config = SettingsConfigDict(
        env_prefix="HUANXIN_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    # ── System identity ─────────────────────────────────────────────
    name: str = "HUANXIN"
    version: str = "0.1.0"
    greeting: str = "At your service, sir."

    # ── Paths ───────────────────────────────────────────────────────
    # ``data_dir`` is the root for audit.db / approval.db / cost_records.json
    # / outcome_records.json / version snapshots / prompt templates.
    # Defaults to the current working directory; override via HUANXIN_DATA_DIR
    # (the container image sets it to /app/data for persistence).
    data_dir: Path = Path(".")
    log_dir: Path = Path("./logs")
    # ``court_path`` is the directory holding huanxin.db (the court main DB).
    court_path: str = ""

    # ── API (single FastAPI service) ───────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    enable_api: bool = False

    # ── Auto-start / scheduling ─────────────────────────────────────
    auto_schedule: bool = True
    auto_seed_ministers: bool = True
    auto_evolve_interval_minutes: float = 5.0
    auto_evolve_cycles: int = 1
    auto_tasks_interval_minutes: float = 3.0

    # ── Evolution / breeding ────────────────────────────────────────
    min_ministers: int = 3
    max_ministers: int = 20
    genome_path: str = ""
    history_path: str = ""
    crossover_rate: float = 0.6
    elitism_count: int = 2
    enable_auto_breeding: bool = True

    # ── Sub-configs ─────────────────────────────────────────────────
    dashboard: DashboardConfig = DashboardConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    evolution: EvolutionConfig = EvolutionConfig()
    capability: CapabilityConfig = CapabilityConfig()
    database: DatabaseConfig = DatabaseConfig()
    model: ModelConfig = ModelConfig()
    memory: MemoryConfig = MemoryConfig()
    sandbox: SandboxConfig = SandboxConfig()

    # ── Persistence / logging / runtime ─────────────────────────────
    log_level: str = "INFO"
    max_task_timeout: float = 30.0
    max_context_tokens: int = 8192
    compression_strategy: str = "auto"  # auto | summarize | extract | prune | hybrid

    # ── Ministers ───────────────────────────────────────────────────
    seed_ministers: list = DEFAULT_SEED_MINISTERS

    # ── Feature flags (preserved from legacy HUANXINConfig) ──────────
    domains_enabled: list = DEFAULT_DOMAINS_ENABLED
    web_server_enabled: bool = True
    websocket_enabled: bool = True
    voice_interface: bool = False
    multi_modal: bool = True
    scheduled_tasks: bool = True
    auto_updates: bool = False
    require_authentication: bool = True
    allowed_users: list = []
    encryption_key_path: str = ""
    audit_logging: bool = True

    # ── Multi-user registration (default OFF; enable via env) ───────
    # When False, /api/auth/register returns 403 and only pre-provisioned
    # admin credentials may log in. See docs/AUTH.md (task A-07).
    open_registration: bool = False


# ══════════════════════════════════════════════════════════════════
# Serialization helpers
# ══════════════════════════════════════════════════════════════════


def _config_to_dict(config: HuanxinConfig) -> dict:
    """Convert config object to a JSON-serializable dict.

    Uses pydantic's ``model_dump(mode="json")`` so that ``Path`` objects,
    ``tuple`` fields and nested sub-models are all emitted in a
    JSON-compatible form.
    """
    return config.model_dump(mode="json")


def _is_tuple_field(model: BaseModel, field_name: str) -> bool:
    """Return True if ``field_name`` on ``model`` is declared as a tuple."""
    annotation = type(model).model_fields[field_name].annotation
    return annotation is tuple or getattr(annotation, "__name__", "") == "tuple"


def _apply_to_model(model: BaseModel, raw: dict) -> None:
    """Recursively apply a (possibly case-insensitive) raw dict onto a sub-model."""
    if not isinstance(raw, dict):
        return
    fields = type(model).model_fields
    for key, value in raw.items():
        field_name = next(
            (f for f in fields if f == key or f.lower() == str(key).lower()),
            None,
        )
        if field_name is None:
            continue
        current = getattr(model, field_name)
        if isinstance(current, BaseModel) and isinstance(value, dict):
            _apply_to_model(current, value)
        elif _is_tuple_field(model, field_name) and isinstance(value, (list, tuple)):
            setattr(model, field_name, tuple(value))
        else:
            setattr(model, field_name, value)


def _apply_raw_config(config: HuanxinConfig, raw: dict) -> None:
    """Apply raw dict values onto a config object, only overriding present keys.

    Keys are matched case-insensitively. Nested sub-models are handled
    recursively. ``tuple``-typed fields (e.g. ``merit_delta_range``) are
    coerced from lists. Missing keys fall back to their existing/default value.
    """
    if not isinstance(raw, dict):
        return
    fields = type(config).model_fields
    for key, value in raw.items():
        field_name = next(
            (f for f in fields if f == key or f.lower() == str(key).lower()),
            None,
        )
        if field_name is None:
            continue
        current = getattr(config, field_name)
        if isinstance(current, BaseModel) and isinstance(value, dict):
            _apply_to_model(current, value)
        elif _is_tuple_field(config, field_name) and isinstance(value, (list, tuple)):
            setattr(config, field_name, tuple(value))
        else:
            setattr(config, field_name, value)


# ══════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════


def load_config(config_path: str = "huanxin.yaml") -> HuanxinConfig:
    """Load config, merging a legacy ``huanxin.yaml`` file onto env + defaults.

    Priority: explicit ``huanxin.yaml`` values override environment variables,
    which in turn override built-in defaults. When the file is absent, a plain
    :class:`HuanxinConfig` (env + defaults) is returned.

    Args:
        config_path: Path to the config file (JSON inside YAML, or plain JSON).

    Returns:
        HuanxinConfig with merged values.
    """
    config = HuanxinConfig()
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "[config] 读取配置文件 %s 失败，回退到默认配置: %s", config_path, exc
            )
            return config
        _apply_raw_config(config, raw)
    return config


def save_default_config(config_path: str = "huanxin.yaml") -> bool:
    """Write default config to disk if the file does not already exist.

    Returns True if a new file was created, False if it already existed.
    """
    if os.path.exists(config_path):
        return False

    config = HuanxinConfig()
    raw = _config_to_dict(config)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
    return True
