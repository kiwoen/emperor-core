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
from enum import Enum
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
    # P1.4: evolution safety gate (opt-in). When set, Court.evolve stops
    # spawning further cycles the moment the breaker trips (merit collapse
    # or cost overrun), so a "thawed" court can never run away.
    circuit_breaker: Any = None
    # P1.4: promotion gate (opt-in). When set, SurvivalMechanism promotes a
    # shadow minister only after ``required_consecutive_gains`` consecutive
    # merit increases — no more reward-hacking on a single lucky cycle.
    promotion_gate: Any = None
    # P0.3: automatic last-place elimination is FROZEN by default.
    #
    # The merit signal that drives elimination used to be a length-based
    # heuristic (see jarvis.court.fitness), which means every minister the
    # court ever "evolved away" was removed on a fabricated signal.  Until
    # RealTaskFitness has accumulated enough real outcomes, the court runs
    # selection in dry-run: decisions are recorded to the evolution history
    # so they can be reviewed, but no minister is actually removed.
    #
    # Set to True to re-arm destructive selection.
    enable_auto_elimination: bool = False


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

        self._db: Any = None

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
            enabled=cfg.enable_auto_elimination,
            promotion_gate=cfg.promotion_gate,
        )
        if not cfg.enable_auto_elimination:
            logger.warning(
                "[Court] 自动末位淘汰已冻结 (enable_auto_elimination=False)："
                "淘汰决策仅记录到 evolution_history，不会移除大臣"
            )

        self._inspector: Any = None
        self._config = cfg
        self._minister_seq: int = 0
        # P1.4: evolution safety breaker (may be None → no gating).
        self._circuit_breaker = cfg.circuit_breaker

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
        # P1.4: when a CircuitBreaker is installed, drive the evolution loop
        # one cycle at a time so we can HALT the moment it trips — a "thawed"
        # court must never be allowed to run away on a collapsing merit signal.
        if self._circuit_breaker is not None:
            return self._evolve_with_breaker(n_cycles)

        if self._db is not None:
            events_before = len(self._sm._events)
        result = self._sm.emperor_evolve(n_cycles)
        if self._db is not None:
            for event in self._sm._events[events_before:]:
                try:
                    self._db.save_evolution(
                        generation=self._sm._cycle_count,
                        minister_name=event.minister,
                        merit_before=event.previous_merit,
                        merit_after=event.new_merit,
                        delta=event.new_merit - event.previous_merit,
                    )
                except Exception:
                    logger.exception(
                        "[Court] Failed to persist evolution for '%s'",
                        event.minister,
                    )
        return result

    def _evolve_with_breaker(self, n_cycles: int) -> dict:
        """Cycle-by-cycle evolution with a hard CircuitBreaker stop.

        Returns the underlying SurvivalMechanism result of the *last* executed
        cycle, augmented with ``halted`` / ``trip_reason`` so callers can see
        that evolution was cut short by the safety gate.

        Note: ``run_cycle`` returns an :class:`EvolutionReport` dataclass, but
        this method must attach ``halted``/``trip_reason`` keys — so the cycle
        result is normalised to a plain dict first.  (Previously the code did
        ``last_result["halted"] = True`` directly on the dataclass, which raised
        ``TypeError`` *precisely when the breaker tripped* — i.e. the safety gate
        would itself crash the loop.  Regression-covered by
        ``test_court_evolve_real_breaker_trip_returns_dict``.)
        """
        breaker = self._circuit_breaker
        last_result: dict = {}
        for _ in range(n_cycles):
            if breaker.is_open:
                logger.error(
                    "[Court] 进化已在熔断状态下中止（%s），不再执行后续轮",
                    breaker._last_reason,
                )
                last_result.setdefault("halted", True)
                last_result.setdefault("trip_reason", breaker._last_reason)
                break
            last_result = self._cycle_result_as_dict(self.run_cycle())
            decision = breaker.record(
                self.cycle, self.avg_merit,
            )
            if decision.open:
                last_result["halted"] = True
                last_result["trip_reason"] = decision.reason
                logger.error(
                    "[Court] 进化被 CircuitBreaker 熔断中止：%s",
                    decision.reason,
                )
                break
        return last_result

    @staticmethod
    def _cycle_result_as_dict(result: Any) -> dict:
        """Normalise one cycle's result to a plain dict.

        ``run_cycle`` may return an :class:`EvolutionReport` dataclass (real
        path) or a dict (tests / alternative backends).  Always return a dict
        so callers can attach ``halted`` / ``trip_reason`` uniformly.
        """
        if isinstance(result, dict):
            return dict(result)
        return {
            "cycle": getattr(result, "cycle", None),
            "active_count": getattr(result, "active_count", None),
            "shadow_count": getattr(result, "shadow_count", None),
            "eliminated_count": getattr(result, "eliminated_count", None),
            "new_spawns": getattr(result, "new_spawns", None),
            "actions_taken": [
                (a.action.value if isinstance(getattr(a, "action", None), Enum)
                 else str(getattr(a, "action", a)))
                for a in getattr(result, "actions_taken", []) or []
            ],
            "systemic_issues": list(getattr(result, "systemic_issues", []) or []),
            "recommendations": list(getattr(result, "recommendations", []) or []),
        }

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

    @property
    def success_rate(self) -> float:
        """Aggregate success rate across all dispatch records (0.0-1.0)."""
        board = self._merit_board
        fn = getattr(board, "success_rate", None)
        # SlidingMeritBoard wraps the real MeritBoard as `.board` but does not
        # delegate every aggregate helper — fall through to the wrapped board.
        if fn is None:
            fn = getattr(getattr(board, "board", None), "success_rate", None)
        if fn is None:
            return 0.0
        return float(fn())

    @property
    def avg_merit(self) -> float:
        """Average merit across all ministers (0.0+)."""
        ranking = self.merit_ranking
        if not ranking:
            return 0.0
        return sum(self._report_merit(m) for m in ranking) / len(ranking)

    @staticmethod
    def _report_merit(report: Any) -> float:
        """Effective merit from a MeritReport / SlidingMeritReport.

        ``MeritReport`` exposes ``merit_score``; ``SlidingMeritReport`` adds
        ``windowed_merit`` (the effective score under sliding-window merit).
        Prefer the windowed value when present.  (Previously this read a
        non-existent ``.merit`` attribute, crashing ``avg_merit`` — and thus the
        CircuitBreaker path — on any real court with registered ministers.)
        """
        for attr in ("windowed_merit", "merit_score", "merit"):
            val = getattr(report, attr, None)
            if val is not None:
                return float(val)
        return 0.0

    @property
    def min_ministers(self) -> int:
        return self._config.min_ministers

    @property
    def max_ministers(self) -> int:
        return self._config.max_ministers

    @property
    def crossover_rate(self) -> float:
        return self._config.crossover_rate

    @property
    def db(self) -> Optional[Any]:
        """The Database instance (may be None if not injected)."""
        return self._db

    @db.setter
    def db(self, value: Optional[Any]) -> None:
        self._db = value

    # ── Persistence ───────────────────────────────────────────────

    def save_genomes(self, path: Optional[str] = None) -> Optional[str]:
        return self._sm.save_genomes(path)

    def load_genomes(self, path: str) -> Any:
        from jarvis.court.genome_store import GenomeStore
        from jarvis.court.evolution import MinisterStatus
        genomes, meta = GenomeStore.load(path)
        # 替换式载入：把整组基因整体替换为存档内容（而非按名合并），
        # 这样「续跑」与「回滚」语义一致——载入即代表系统此刻的完整基因状态。
        self._sm._genomes.clear()
        self._sm._statuses.clear()
        for g in genomes:
            self._sm._genomes[g.name] = g
            self._sm._statuses[g.name] = MinisterStatus.ACTIVE
        return genomes, meta

    def genome_state_payload(self) -> dict:
        """Snapshot all living genomes as a GenomeStore-style payload dict.

        Returns ``{"version": 1, "metadata": {...}, "genomes": [...]}`` — the
        exact shape :class:`~jarvis.court.genome_store.GenomeStore` persists.
        Used by the self-evolution loop to (a) checkpoint progress and (b) build
        a real, reviewable diff of what the system changed about *itself*.
        """
        from jarvis.court.evolution import MinisterStatus
        from jarvis.court.genome_store import GenomeStore

        living = [
            g for name, g in self._sm._genomes.items()
            if self._sm._statuses.get(name) != MinisterStatus.ELIMINATED
        ]
        active = sum(1 for s in self._sm._statuses.values() if s == MinisterStatus.ACTIVE)
        shadow = sum(1 for s in self._sm._statuses.values() if s == MinisterStatus.SHADOW)
        return {
            "version": 1,
            "metadata": {
                "cycle": self._sm._cycle_count,
                "active_count": active,
                "shadow_count": shadow,
                "total_genomes": len(living),
            },
            "genomes": [GenomeStore.to_dict(g) for g in living],
        }

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
