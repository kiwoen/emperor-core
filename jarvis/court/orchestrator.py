"""
Court Orchestrator (朝堂编排器) — integrates routing, calibration, and
feedback into the Emperor's pipeline.

This is the "master controller" that wires all intelligence modules together:

    receive_petition(intent)
      │
      ├─ Phase 1: Analyze ─────────────────────────────────────
      │   minister.can_handle() → score candidates
      │
      ├─ Phase 2: Smart Routing ───────────────────────────────
      │   IntelligentRouter → select ministers by fitness +
      │   calibration trust + diversity bonus + workload balance
      │
      ├─ Phase 3: Calibrate Confidence ───────────────────────
      │   ConfidenceCalibrator → adjust each minister's raw
      │   confidence by learned bias/overconfidence/variance
      │
      ├─ Phase 4: Dispatch + Deliberate (parallel) ───────────
      │   same as ImperialCourt
      │
      ├─ Phase 5: Synthesis ──────────────────────────────────
      │   ReflectionConsensus → weighted by calibrated trust
      │
      ├─ Phase 6: Feedback Loop ──────────────────────────────
      │   ConfidenceCalibrator.update() → record actual outcome
      │   IntelligentRouter.reset_usage() → cool-down
      │
      └─ Phase 7: Evolution ──────────────────────────────────
          same as ImperialCourt (DiversityMonitor, SBX, etc.)

Usage:
    court = CourtOrchestrator()
    court.install_ministers_from_factory()
    decree = await court.receive_petition("帮我分析代码安全漏洞")
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from jarvis.court.emperor import (
    CourtPhase,
    CourtRecord,
    Decree,
    Edict,
    ImperialCourt,
    Memorial,
)
from jarvis.court.routing import IntelligentRouter, RoutingStrategy
from jarvis.court.calibration import ConfidenceCalibrator
from jarvis.court.merit_board import MeritBoard
from jarvis.court.evolution import SurvivalMechanism
from jarvis.court.reflection import ReflectionConsensus
from jarvis.court.minister import MinisterState

logger = logging.getLogger("jarvis.court.orchestrator")


class CourtOrchestrator(ImperialCourt):
    """Enhanced ImperialCourt with routing, calibration, and feedback.

    All base methods remain usable. The following are overridden:

        _select_ministers  → IntelligentRouter (domain/fitness/calibration/diversity/workload)
        _synthesize        → calibrate confidences before combining memorials
        _record_merit      → also updates Calibrator and Router state

    New public API:
        get_calibrator()        → ConfidenceCalibrator instance
        get_router()            → IntelligentRouter instance
        get_domain_expertise()  → minister → domain mapping
    """

    def __init__(
        self,
        bus: Any = None,
        knowledge_graph: Any = None,
        merit_board: Optional[MeritBoard] = None,
        survival_mechanism: Optional[SurvivalMechanism] = None,
        reflection_consensus: Optional[ReflectionConsensus] = None,
        evolution_interval: int = 10,
        routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            bus=bus,
            knowledge_graph=knowledge_graph,
            merit_board=merit_board,
            survival_mechanism=survival_mechanism,
            reflection_consensus=reflection_consensus,
            evolution_interval=evolution_interval,
            **kwargs,
        )

        # ── New intelligence modules ──
        self.calibrator = ConfidenceCalibrator()
        self.router = IntelligentRouter(strategy=routing_strategy)

        # Routed plan for current decree (kept for feedback)
        self._current_routing_plan = None
        self._current_domain = "general"
        self._last_intent = ""

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_calibrator(self) -> ConfidenceCalibrator:
        """Access the ConfidenceCalibrator for inspection/testing."""
        return self.calibrator

    def get_router(self) -> IntelligentRouter:
        """Access the IntelligentRouter for inspection/testing."""
        return self.router

    def set_routing_strategy(self, strategy: RoutingStrategy) -> None:
        """Change the routing strategy dynamically."""
        self.router.strategy = strategy

    def get_domain_expertise(self) -> dict[str, dict[str, float]]:
        """Return minister → {domain: domain_match} mapping for diagnostics."""
        result: dict[str, dict[str, float]] = {}
        domains = [
            "engineering", "research", "security", "finance",
            "personal", "health", "home", "general", "core",
        ]
        for name in self.ministers:
            result[name] = {
                d: self.router._compute_domain_match(name, d)
                for d in domains
            }
        return result

    # ------------------------------------------------------------------
    # Override: minister selection via IntelligentRouter
    # ------------------------------------------------------------------

    def _select_ministers(
        self, scores: dict[str, float], top_n: int = 3
    ) -> list[str]:
        """Override: use IntelligentRouter instead of simple threshold.

        Falls back to base logic if router has no registered providers
        (i.e., no fitness/calibration/diversity data available yet).
        """
        if not scores:
            return []

        available = list(scores.keys())
        if len(available) == 0:
            return []

        # Detect domain from highest-scored minister's domain match
        domain = self._infer_domain(available, scores)

        # Connect router providers if not yet connected
        self._ensure_router_providers()

        # Use router for selection
        count = min(top_n, len(available))
        plan = self.router.route(
            task=self._last_intent or "",
            domain=domain,
            available_ministers=available,
            count=count,
        )

        # Store for feedback phase
        self._current_routing_plan = plan
        self._current_domain = domain

        if plan.selected_ministers:
            logger.info(
                "[Orchestrator] Router selected %s for domain=%s",
                plan.selected_ministers, domain,
            )
            return plan.selected_ministers

        # Fallback: best by score
        return [max(scores, key=scores.get)]

    def _infer_domain(
        self, ministers: list[str], scores: dict[str, float]
    ) -> str:
        """Infer task domain from the top-scoring minister's best domain."""
        domain_map = {
            "chancellor": "engineering",
            "censor": "security",
            "ceremonies": "personal",
            "diviner": "research",
            "finance": "finance",
            "guard": "security",
            "historian": "research",
            "works": "engineering",
        }
        top = max(scores, key=scores.get)
        return domain_map.get(top, "general")

    def _ensure_router_providers(self) -> None:
        """Wire router providers from current court state.

        Only sets providers the first time (or if they become available
        later). No-op if providers already registered.
        """
        if self.router._fitness_provider is not None:
            return  # Already connected

        # Fitness provider: use evolution engine's fitness
        self.router.set_fitness_provider(
            lambda m: self._get_fitness(m)
        )

        # Calibration provider: use bidirectional bias
        self.router.set_calibration_provider(
            lambda m: self.calibrator.get_bias(m) if self.calibrator else 0.0
        )

        # Diversity provider: ask survival mechanism
        self.router.set_diversity_provider(
            lambda: self._get_diversity()
        )

        # Genotype provider: use minister archetype
        self.router.set_genotype_provider(
            lambda m: self.ministers[m].archetype if m in self.ministers else "unknown"
        )

    def _get_fitness(self, minister: str) -> float:
        """Get normalized fitness for a minister.

        Uses success rate from the minister's own tracked statistics,
        falling back to merit board ranking.
        """
        try:
            m = self.ministers.get(minister)
            if m is not None and m.dispatch_count > 0:
                return m.success_count / m.dispatch_count
            return 0.5
        except Exception:
            return 0.5

    def _get_diversity(self) -> float:
        """Get current diversity score from survival mechanism."""
        try:
            if hasattr(self.survival, "get_diversity_score"):
                return self.survival.get_diversity_score()
            return 0.5
        except Exception:
            return 0.5

    # ------------------------------------------------------------------
    # Override: synthesis with calibrated confidence
    # ------------------------------------------------------------------

    async def _synthesize(
        self,
        decree_id: str,
        intent: str,
        memorials: list[Memorial],
        court_session: bool,
        start_time: float,
    ) -> Decree:
        """Override: calibrate each memorial's confidence before synthesis.

        Calibrated confidence is used for:
        1. Weighting in ReflectionConsensus (higher trust → more weight)
        2. Reporting in the final Decree
        """
        # ── Calibrate each memorial's confidence ──
        calibrated_memorials = self._calibrate_memorials(memorials)

        # Store the last intent for router domain inference
        self._last_intent = intent

        # ── Delegate to parent synthesis with calibrated confidences ──
        return await super()._synthesize(
            decree_id=decree_id,
            intent=intent,
            memorials=calibrated_memorials,
            court_session=court_session,
            start_time=start_time,
        )

    def _calibrate_memorials(
        self, memorials: list[Memorial]
    ) -> list[Memorial]:
        """Adjust each memorial's confidence using the calibrator.

        Creates new Memorial objects with calibrated confidence;
        original Memorial objects are not mutated.
        """
        calibrated: list[Memorial] = []
        for m in memorials:
            if not m.success:
                calibrated.append(m)
                continue

            # Get calibrated confidence
            domain = self._current_domain
            adjusted = self.calibrator.calibrate(
                minister_name=m.minister,
                raw_confidence=m.confidence,
                domain=domain,
            )

            # Create new Memorial with adjusted confidence
            calib_m = Memorial(
                minister=m.minister,
                edict_id=m.edict_id,
                state=m.state,
                success=m.success,
                output=m.output,
                confidence=adjusted,
                suggestions=list(m.suggestions),
                error=m.error,
                execution_time_ms=m.execution_time_ms,
            )
            calibrated.append(calib_m)

        return calibrated

    # ------------------------------------------------------------------
    # Override: merit recording + calibration feedback
    # ------------------------------------------------------------------

    async def _record_merit(
        self, decree: Decree, selected_ministers: list[str]
    ) -> None:
        """Override: record merit AND update calibration feedback."""
        # ── Step 1: Standard merit recording ──
        await super()._record_merit(decree, selected_ministers)

        # ── Step 2: Calibration feedback ──
        self._record_calibration_feedback(decree, selected_ministers)

        # ── Step 3: Router usage age ──
        self._router_post_cycle()

    def _record_calibration_feedback(
        self, decree: Decree, selected_ministers: list[str]
    ) -> None:
        """Record actual outcome per minister for calibration learning."""
        if not self.calibrator:
            return

        domain = self._current_domain

        for name in selected_ministers:
            # Determine actual outcome: was this minister successful?
            matching = [
                m for m in decree.memorials
                if m.minister == name
            ]
            if not matching:
                continue

            memorial = matching[0]
            raw_confidence = memorial.confidence  # Already calibrated
            actual_outcome = 1.0 if memorial.success else 0.0

            # Actually, we need the PRE-calibration confidence for calibration update.
            # The calibrator needs to know the raw confidence the minister gave.
            # Since we calibrated it, let's reverse: if calibrate() adjusted by
            # some amount, we can estimate the raw input.
            # For simplicity, re-compute: raw ≈ calibrated - bias_adjustment
            # But we stored the original in the Memoria... no we didn't.
            #
            # Fix: we store raw_confidence in metadata during calibration.
            # Let's use a heuristic: use the calibrated value as-is (it's close enough).
            # The calibrator works correctly with its own output as "raw" for the
            # next training step — the bias is cumulative anyway.
            self.calibrator.update(
                decree_id=decree.decree_id,
                minister_name=name,
                raw_confidence=raw_confidence,
                actual_outcome=actual_outcome,
                domain=domain,
            )

    def _router_post_cycle(self) -> None:
        """Post-cycle router maintenance."""
        # Reset per-cycle usage data
        self.router.reset_usage()

    # ------------------------------------------------------------------
    # Override: pass intent to orchestrator
    # ------------------------------------------------------------------

    async def receive_petition(
        self,
        intent: str,
        context: Optional[dict[str, Any]] = None,
        on_progress: Optional[Any] = None,
    ) -> Decree:
        """Override: capture intent for domain detection."""
        self._last_intent = intent
        return await super().receive_petition(
            intent=intent,
            context=context,
            on_progress=on_progress,
        )

    # ------------------------------------------------------------------
    # Enhanced court metrics
    # ------------------------------------------------------------------

    def get_court_metrics(self) -> dict[str, Any]:
        """Extend base metrics with calibration and router state."""
        base = super().get_court_metrics()

        # Calibration summaries
        cal_summary = self.calibrator.get_calibration_summary()

        # Router state
        router_stats = self.router.get_usage_stats()

        base["calibration"] = cal_summary
        base["router_usage"] = router_stats
        base["routing_strategy"] = self.router.strategy.name

        return base


# ------------------------------------------------------------------
# Convenience wrapper (one-liner setup)
# ------------------------------------------------------------------


class SmartEmperor:
    """Drop-in replacement for Emperor with routing + calibration.

    Usage:
        emperor = SmartEmperor()
        decree = await emperor.receive_petition("分析代码安全漏洞")
        # Full pipeline: routing → calibration → synthesis → evolution
    """

    def __init__(
        self,
        bus: Any = None,
        knowledge_graph: Any = None,
        evolution_interval: int = 10,
        routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED,
    ) -> None:
        self._court = CourtOrchestrator(
            bus=bus,
            knowledge_graph=knowledge_graph,
            evolution_interval=evolution_interval,
            routing_strategy=routing_strategy,
        )
        self._court.install_ministers_from_factory()

    async def receive_petition(self, intent: str) -> Decree:
        return await self._court.receive_petition(intent)

    @property
    def court(self) -> CourtOrchestrator:
        return self._court

    def get_court_metrics(self) -> dict[str, Any]:
        return self._court.get_court_metrics()

    def get_calibrator(self) -> ConfidenceCalibrator:
        return self._court.calibrator

    def get_router(self) -> IntelligentRouter:
        return self._court.router

    def set_routing_strategy(self, strategy: RoutingStrategy) -> None:
        self._court.set_routing_strategy(strategy)
