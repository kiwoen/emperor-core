"""Declarative configuration for the evolutionary court.

SurvivalConfig is a dataclass that holds every tunable parameter of the
SurvivalMechanism.  It can be created programmatically, loaded from a YAML
file, or serialised back — enabling:

- Git-tracked experiment configs (version your evolution parameters!)
- Hot-reload with config watch (future)
- Reproducible evolution runs across machines

Design:
- Flat dataclass, no inheritance — every field maps 1:1 to a
  SurvivalMechanism __init__ parameter.
- from_yaml() / to_yaml() for file I/O (pyyaml required).
- from_dict() / to_dict() for programmatic use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from jarvis.court.evolution import (
    CrossoverMode,
    EliteTurnoverMode,
    EvolutionRateMode,
    WindowMode,
)


@dataclass
class SurvivalConfig:
    """All knobs for SurvivalMechanism in one place."""

    # ── Core counts ────────────────────────────────────────────────
    elitism_count: int = 2
    crossover_rate: float = 0.6

    # ── Crossover ──────────────────────────────────────────────────
    crossover_mode: str = "sbx"
    sbx_eta: float = 15.0

    # ── Adaptive elite turnover ────────────────────────────────────
    turnover_mode: str = "adaptive"
    min_elites: int = 1
    max_elites: int = 5

    # ── Adaptive evolution rate ────────────────────────────────────
    rate_mode: str = "adaptive"
    # rate_config is dynamic (AdaptiveRateConfig), not serialised here

    # ── Sliding merit window ───────────────────────────────────────
    enable_sliding_merit: bool = True
    sliding_window_size: int = 50
    sliding_window_mode: str = "hard_cutoff"

    # ── AutoBreeder ────────────────────────────────────────────────
    enable_auto_breeding: bool = True
    breeding_cooldown: int = 5
    max_breed_per_cycle: int = 3

    # ── Genome persistence ─────────────────────────────────────────
    genome_path: str = ""

    # ── Stability tracker ──────────────────────────────────────────
    # (window size is hard-coded in StabilityTracker for now)

    # ── Serialisation ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Export as plain dict (suitable for JSON/YAML)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SurvivalConfig":
        """Construct from a dict, ignoring unrecognised keys."""
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SurvivalConfig":
        """Load from a YAML file.  Requires pyyaml."""
        import yaml  # lazy import — only needed for YAML support
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    def to_yaml(self, path: str | Path) -> None:
        """Write config to a YAML file."""
        import yaml
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)

    @classmethod
    def from_json(cls, path: str | Path) -> "SurvivalConfig":
        """Load from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def to_json(self, path: str | Path) -> None:
        """Write config to a JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    # ── Mode conversion helpers ────────────────────────────────────

    @property
    def crossover_mode_enum(self) -> CrossoverMode:
        return CrossoverMode[self.crossover_mode.upper()]

    @property
    def turnover_mode_enum(self) -> EliteTurnoverMode:
        return EliteTurnoverMode[self.turnover_mode.upper()]

    @property
    def rate_mode_enum(self) -> EvolutionRateMode:
        return EvolutionRateMode[self.rate_mode.upper()]

    @property
    def sliding_window_mode_enum(self) -> WindowMode:
        return WindowMode[self.sliding_window_mode.upper()]
