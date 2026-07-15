"""Court facade — one-stop entry point for the evolutionary system.

Court bundles MeritBoard, SurvivalMechanism, EvolutionHistory, and
CourtInspector into a single coordinated interface.

Usage:
    court = Court()
    court.register("alpha", domain="math", temperature=0.7)
    court.register("beta",  domain="code", temperature=0.8)
    court.evolve(10)
    print(court.summary())
    court.history.to_csv("evolution.csv")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("jarvis.court.facade")


@dataclass
class CourtConfig:
    """All-in-one configuration for a Court instance."""

    min_ministers: int = 3
    max_ministers: int = 20
    enable_sliding_merit: bool = True
    sliding_window_size: int = 20
    sliding_window_mode: str = "exp_decay"
    elitism_count: int = 2
    crossover_rate: float = 0.6
    crossover_mode: str = "sbx"
    sbx_eta: float = 2.0
    turnover_mode: str = "adaptive"
    min_elites: int = 1
    max_elites: int = 4
    rate_mode: str = "adaptive"
    stability_blend: float = 0.20
    enable_auto_breeding: bool = True
    breeding_cooldown: int = 2
    max_breed_per_cycle: int = 2
    genome_path: Optional[str] = None


class Court:
    """The evolutionary court — one class to rule them all."""

    def __init__(self, config: Optional[CourtConfig] = None) -> None:
        from jarvis.court.merit_board import MeritBoard
        from jarvis.court.sliding_merit import SlidingMeritBoard, WindowMode
        from jarvis.court.evolution import (
            SurvivalMechanism, CrossoverMode,
            EliteTurnoverMode, EvolutionRateMode,
        )
        from jarvis.court.history import EvolutionHistory

        cfg = config or CourtConfig()

        base_board = MeritBoard()
        window_mode = (
            WindowMode.HARD_CUTOFF if cfg.sliding_window_mode == "hard_cutoff"
            else WindowMode.EXP_DECAY
        )
        self._merit_board = (
            SlidingMeritBoard(base_board,
                              window_size=cfg.sliding_window_size,
                              mode=window_mode)
            if cfg.enable_sliding_merit
            else base_board
        )

        cmode = CrossoverMode.SBX if cfg.crossover_mode == "sbx" else CrossoverMode.UNIFORM
        tmode = EliteTurnoverMode.ADAPTIVE if cfg.turnover_mode == "adaptive" else EliteTurnoverMode.FIXED
        rmode = EvolutionRateMode.ADAPTIVE

        self.history = EvolutionHistory()

        self._sm = SurvivalMechanism(
            merit_board=self._merit_board,
            elitism_count=cfg.elitism_count,
            crossover_rate=cfg.crossover_rate,
            crossover_mode=cmode,
            sbx_eta=cfg.sbx_eta,
            turnover_mode=tmode,
            min_elites=cfg.min_elites,
            max_elites=cfg.max_elites,
            rate_mode=rmode,
            enable_auto_breeding=cfg.enable_auto_breeding,
            breeding_cooldown=cfg.breeding_cooldown,
            max_breed_per_cycle=cfg.max_breed_per_cycle,
            genome_path=cfg.genome_path,
            history=self.history,
        )

        self._inspector: Any = None
        self._config = cfg
        self._minister_seq: int = 0

    # ── Registration ──────────────────────────────────────────────

    def register(
        self, name: Optional[str] = None, *,
        domain: str = "general",
        temperature: float = 0.7,
        confidence_baseline: float = 0.75,
    ) -> str:
        if name is None:
            name = f"m{self._minister_seq}"
            self._minister_seq += 1
        else:
            self._minister_seq = max(self._minister_seq, self._minister_seq)
        self._sm.register_minister(
            name, domain, temperature,
            confidence_baseline=confidence_baseline,
        )
        logger.info("[Court] Registered '%s' (domain=%s)", name, domain)
        return name

    def register_many(self, specs: list[dict]) -> list[str]:
        names = []
        for spec in specs:
            names.append(self.register(
                name=spec.get("name"),
                domain=spec.get("domain", "general"),
                temperature=spec.get("temperature", 0.7),
                confidence_baseline=spec.get("confidence_baseline", 0.75),
            ))
        return names

    # ── Evolution ─────────────────────────────────────────────────

    def evolve(self, n_cycles: int = 1) -> dict:
        return self._sm.emperor_evolve(n_cycles)

    def run_cycle(self) -> Any:
        return self._sm.run_evolution_cycle()

    # ── Merit ─────────────────────────────────────────────────────

    def record_dispatch(
        self, minister: str, edict_id: str, intent: str,
        success: bool, confidence: float, execution_time_ms: float = 0.0,
    ) -> None:
        self._merit_board.record_dispatch(
            minister, edict_id, intent, success, confidence,
            execution_time_ms=execution_time_ms,
        )

    def record_feedback(self, minister: str, edict_id: str, score: float) -> bool:
        return self._merit_board.record_feedback(minister, edict_id, score)

    # ── Inspection ────────────────────────────────────────────────

    @property
    def inspect(self) -> Any:
        if self._inspector is None:
            from jarvis.court.inspector import CourtInspector
            self._inspector = CourtInspector(self._sm)
        return self._inspector

    def summary(self) -> str:
        return self.inspect.summary()

    # ── State ─────────────────────────────────────────────────────

    @property
    def cycle(self) -> int:
        return self._sm._cycle_count

    @property
    def active_ministers(self) -> list[str]:
        return self._sm.get_active_ministers()

    @property
    def config(self) -> CourtConfig:
        return self._config

    @property
    def merit_ranking(self) -> list[Any]:
        return self._merit_board.get_ranking()

    # ── Persistence ───────────────────────────────────────────────

    def save_genomes(self) -> Optional[str]:
        return self._sm.save_genomes()

    def load_genomes(self, path: str) -> Any:
        from jarvis.court.genome_store import GenomeStore
        from jarvis.court.evolution import MinisterStatus
        genomes, meta = GenomeStore.load(path)
        for g in genomes:
            self._sm._genomes[g.name] = g
            self._sm._statuses[g.name] = MinisterStatus.ACTIVE
        return genomes, meta

    def save_history(self, path: str) -> None:
        """Save evolution history to JSON file."""
        import json
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.history.to_json(), encoding="utf-8")

    def load_history(self, path: str) -> None:
        """Load evolution history from JSON file."""
        self.history._records.clear()
        self.history._read_from_json(path)
