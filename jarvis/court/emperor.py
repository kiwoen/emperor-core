"""
Emperor (天子) — the sovereign orchestrator of the Imperial Court.

The Emperor receives user intents, analyzes them, dispatches edicts to
the appropriate minister(s), gathers memorials (reports), and issues
final decrees. The court system supports:

    1. Single-minister dispatch — direct routing
    2. Court session (朝堂议事) — multi-minister collaboration
    3. Minister evolution tracking — performance metrics & feedback
    4. Knowledge accumulation — auto-ingestion into KnowledgeGraph
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from jarvis.court.minister import (
    Edict,
    Memorial,
    Minister,
    MinisterProfile,
    MinisterState,
)

logger = logging.getLogger("jarvis.court.emperor")


@dataclass
class Decree:
    """Final imperial decree — the Emperor's synthesized response to the user.

    This is the structured output returned after all ministers have
    deliberated and the Emperor has weighed their counsel.
    """
    decree_id: str
    intent: str
    success: bool
    output: str                     # The synthesized answer/report
    ministers_consulted: list[str]  # Which ministers were involved
    memorials: list[Memorial]       # Individual minister reports
    confidence: float               # Aggregated confidence
    execution_time_ms: float        # Total end-to-end time
    court_session: bool = False     # Was this a multi-minister session?
    dissenting_opinions: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class CourtRecord:
    """Historical record of a completed decree for minister evolution."""
    decree_id: str
    intent: str
    success: bool
    ministers_involved: list[str]
    primary_minister: str
    confidence: float
    execution_time_ms: float
    timestamp: str


class ImperialCourt:
    """The Emperor's cabinet — manages all ministers and coordinates
    the full decision-making pipeline.

    Usage:
        court = ImperialCourt()
        court.install_ministers_from_factory()
        decree = await court.receive_petition("帮我分析代码安全漏洞")
        print(f"[{decree.confidence:.0%}] {decree.output}")
    """

    def __init__(
        self,
        bus: Any = None,
        knowledge_graph: Any = None,
    ) -> None:
        self.ministers: dict[str, Minister] = {}
        self.records: list[CourtRecord] = []
        self.bus = bus
        self.knowledge_graph = knowledge_graph
        self._lock = asyncio.Lock()
        self._decree_count = 0

    # ------------------------------------------------------------------
    # Minister management
    # ------------------------------------------------------------------

    def install_minister(self, minister: Minister) -> None:
        """Appoint a minister to the court."""
        if self.knowledge_graph is not None:
            minister.set_knowledge_graph(self.knowledge_graph)
        self.ministers[minister.name] = minister
        logger.info("[Emperor] Appointed %s (%s) to the court",
                     minister.name, minister.archetype)

    def dismiss_minister(self, name: str) -> bool:
        """Remove a minister from the court."""
        if name in self.ministers:
            del self.ministers[name]
            logger.info("[Emperor] Dismissed %s from the court", name)
            return True
        return False

    def install_ministers_from_factory(self) -> None:
        """Appoint all eight standard ministers with real-AI-derived profiles.

        Also injects model providers from the ProviderRegistry so ministers
        can use real LLM APIs when API keys are configured.
        """
        from jarvis.court.ministers import create_ministers
        from jarvis.court.providers.registry import get_provider_registry

        registry = get_provider_registry()
        for minister in create_ministers():
            provider = registry.get_provider(minister.name)
            if provider is not None:
                minister.set_provider(provider)
            self.install_minister(minister)

    # ------------------------------------------------------------------
    # Intelligence gathering (分析情报)
    # ------------------------------------------------------------------

    def analyze_petition(self, intent: str) -> dict[str, float]:
        """Analyze incoming user petition — score every minister's suitability.

        Returns dict of minister_name → confidence_score.
        """
        scores: dict[str, float] = {}
        for name, minister in self.ministers.items():
            if minister.state != MinisterState.OFFLINE:
                scores[name] = minister.can_handle(intent)
        return dict(
            sorted(scores.items(), key=lambda x: x[1], reverse=True)
        )

    def _select_ministers(
        self, scores: dict[str, float], top_n: int = 3
    ) -> list[str]:
        """Select up to top_n qualified ministers for a task.

        Routing logic (朝堂议事规则):
        - If top minister has confidence >= 0.7: single-minister dispatch
        - If multiple ministers have confidence >= 0.5: multi-minister court session
        - Default: top 1 minister even if low confidence
        """
        if not scores:
            return []

        top_name = next(iter(scores))
        top_score = scores[top_name]

        if top_score >= 0.7:
            return [top_name]

        # Multiple qualified ministers — hold a court session
        qualified = [
            name for name, score in scores.items()
            if score >= 0.5
        ]
        if len(qualified) >= 2:
            return qualified[:top_n]

        # Fallback: best available
        return [top_name]

    # ------------------------------------------------------------------
    # Main pipeline: receive petition → issue decree
    # ------------------------------------------------------------------

    async def receive_petition(
        self,
        intent: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Decree:
        """The Emperor receives a petition (user request) and returns a decree.

        Full pipeline:
            1. Analyze — which ministers are qualified?
            2. Dispatch — send edicts to selected ministers
            3. Deliberate — ministers process independently (parallel)
            4. Synthesize — Emperor weighs all memorials and decides
            5. Record — archive for evolution tracking
        """
        self._decree_count += 1
        decree_id = f"decree_{self._decree_count}_{uuid.uuid4().hex[:8]}"
        start = time.monotonic()

        logger.info(
            "[Emperor] Receiving petition: decree=%s intent='%s'",
            decree_id, intent[:80],
        )

        # ── Phase 1: Analyze ─────────────────────────────────────────
        scores = self.analyze_petition(intent)
        if not scores:
            return Decree(
                decree_id=decree_id,
                intent=intent,
                success=False,
                output="朝中无可用之臣（无可用大臣处理此任务）。",
                ministers_consulted=[],
                memorials=[],
                confidence=0.0,
                execution_time_ms=(time.monotonic() - start) * 1000,
            )

        selected = self._select_ministers(scores)
        logger.info("[Emperor] Court session with: %s", selected)

        # ── Phase 2: Dispatch edicts ─────────────────────────────────
        edicts: list[Edict] = []
        for name in selected:
            edict = Edict(
                edict_id=f"{decree_id}::{name}",
                intent=intent,
                context=context or {},
                priority=8 if scores[name] >= 0.7 else 5,
                minister=name,
            )
            edicts.append(edict)

        # ── Phase 3: Parallel deliberation ───────────────────────────
        memorials = await asyncio.gather(
            *[self.ministers[e.minister].receive_edict(e) for e in edicts],
            return_exceptions=True,
        )

        # Filter out exceptions
        valid_memorials: list[Memorial] = []
        for m in memorials:
            if isinstance(m, Memorial):
                valid_memorials.append(m)
            else:
                logger.warning("[Emperor] A minister failed: %s", m)

        # ── Phase 4: Synthesize decree ───────────────────────────────
        decree = await self._synthesize(
            decree_id=decree_id,
            intent=intent,
            memorials=valid_memorials,
            court_session=len(selected) > 1,
            start_time=start,
        )

        # ── Phase 5: Auto-ingest into KnowledgeGraph ─────────────────
        if self.knowledge_graph:
            await self._ingest_into_kg(decree)

        # ── Phase 6: Record for evolution ──────────────────────────
        self._archive_decree(decree)

        logger.info(
            "[Emperor] Decree %s issued: success=%s confidence=%.2f ministers=%d",
            decree_id, decree.success, decree.confidence,
            len(decree.ministers_consulted),
        )
        return decree

    async def _synthesize(
        self,
        decree_id: str,
        intent: str,
        memorials: list[Memorial],
        court_session: bool,
        start_time: float,
    ) -> Decree:
        """Weigh all memorials and compose the final decree.

        Decision rules:
        - If all ministers agree: adopt majority output
        - If ministers disagree: present both sides + Emperor decides
        - If only one minister: adopt their output directly
        """
        if not memorials:
            return Decree(
                decree_id=decree_id,
                intent=intent,
                success=False,
                output="诸臣皆默，无策可陈。",
                ministers_consulted=[],
                memorials=[],
                confidence=0.0,
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        successes = [m for m in memorials if m.success]
        failures = [m for m in memorials if not m.success]

        if not successes and failures:
            # All failed — report first error
            return Decree(
                decree_id=decree_id,
                intent=intent,
                success=False,
                output=f"诸臣皆败: {failures[0].error or '未知错误'}",
                ministers_consulted=[m.minister for m in memorials],
                memorials=memorials,
                confidence=0.0,
                execution_time_ms=(time.monotonic() - start_time) * 1000,
                court_session=court_session,
            )

        # Build the decree
        all_ministers = [m.minister for m in memorials]
        avg_confidence = sum(m.confidence for m in successes) / max(1, len(successes))

        # Synthesize output: blend all successful memorials
        if len(successes) == 1:
            output = successes[0].output
            recommendations = successes[0].suggestions
        else:
            # Multi-minister synthesis
            parts = []
            for i, m in enumerate(successes, 1):
                parts.append(f"【{m.minister}】{m.output}")
            output = "\n\n".join(parts)

            # Collect all recommendations
            recommendations = []
            for m in successes:
                recommendations.extend(m.suggestions)

        # Dissenting opinions from failures
        dissenting = [
            f"{m.minister}: {m.error or '未能完成'}"
            for m in failures
        ]

        return Decree(
            decree_id=decree_id,
            intent=intent,
            success=True,
            output=output,
            ministers_consulted=all_ministers,
            memorials=memorials,
            confidence=avg_confidence,
            execution_time_ms=(time.monotonic() - start_time) * 1000,
            court_session=court_session,
            dissenting_opinions=dissenting,
            recommendations=recommendations,
        )

    async def _ingest_into_kg(self, decree: Decree) -> None:
        """Feed decree outcome into KnowledgeGraph for cross-domain learning."""
        if not self.knowledge_graph:
            return
        try:
            # Ingest the intent + domain knowledge
            for m in decree.memorials:
                ingest_text = (
                    f"Emperor decreed: {decree.intent} "
                    f"was handled by {m.minister} "
                    f"with confidence {m.confidence:.2f}"
                )
                await self.knowledge_graph.ingest(
                    ingest_text,
                    domain=f"court::{m.minister}",
                )
        except Exception:
            logger.debug("[Emperor] KG ingestion skipped (non-critical)")

    def _archive_decree(self, decree: Decree) -> None:
        """Archive a decree for evolution tracking."""
        primary = (
            decree.ministers_consulted[0]
            if decree.ministers_consulted
            else "unknown"
        )
        record = CourtRecord(
            decree_id=decree.decree_id,
            intent=decree.intent,
            success=decree.success,
            ministers_involved=decree.ministers_consulted,
            primary_minister=primary,
            confidence=decree.confidence,
            execution_time_ms=decree.execution_time_ms,
            timestamp=decree.timestamp,
        )
        self.records.append(record)

    # ------------------------------------------------------------------
    # Feedback loop
    # ------------------------------------------------------------------

    def send_feedback(
        self, decree_id: str, minister_name: str, score: float
    ) -> None:
        """The Emperor (or user) provides feedback on a minister's work."""
        if minister_name in self.ministers:
            self.ministers[minister_name].record_feedback(decree_id, score)

    # ------------------------------------------------------------------
    # Statistics & court overview
    # ------------------------------------------------------------------

    def get_court_metrics(self) -> dict[str, Any]:
        """Return a full court dashboard — every minister's evolution state."""
        minister_metrics = {}
        for name, minister in self.ministers.items():
            minister_metrics[name] = minister.get_evolution_metrics()

        recent = self.records[-20:]
        overall_success = (
            sum(1 for r in recent if r.success) / max(1, len(recent))
        )

        return {
            "decree_count": self._decree_count,
            "minister_count": len(self.ministers),
            "recent_success_rate": round(overall_success, 3),
            "ministers": minister_metrics,
            "top_performer": self._find_top_performer(),
        }

    def _find_top_performer(self) -> Optional[str]:
        """Identify the best-performing minister by success rate."""
        best_name: Optional[str] = None
        best_rate = -1.0
        for name, minister in self.ministers.items():
            rate = (
                minister.success_count / max(1, minister.dispatch_count)
            )
            if rate > best_rate:
                best_rate = rate
                best_name = name
        return best_name

    def get_minister(self, name: str) -> Optional[Minister]:
        """Look up a minister by court title."""
        return self.ministers.get(name)


# ------------------------------------------------------------------
# Standalone Emperor wrapper (for backward compatibility)
# ------------------------------------------------------------------


class Emperor:
    """Convenience wrapper: Emperor = ImperialCourt + auto-install.

    Usage:
        emperor = Emperor()
        decree = await emperor.receive_petition("分析代码安全漏洞")
    """

    def __init__(
        self,
        bus: Any = None,
        knowledge_graph: Any = None,
    ) -> None:
        self._court = ImperialCourt(bus=bus, knowledge_graph=knowledge_graph)
        self._court.install_ministers_from_factory()

    async def receive_petition(self, intent: str) -> Decree:
        """Shortcut: receive petition → decree."""
        return await self._court.receive_petition(intent)

    @property
    def court(self) -> ImperialCourt:
        return self._court

    def get_court_metrics(self) -> dict[str, Any]:
        return self._court.get_court_metrics()
