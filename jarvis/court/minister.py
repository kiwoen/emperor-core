"""
Minister Base Class — 自治大臣基础类

Each minister is a self-contained AI agent that:
    1. Has a unique capability profile (extracted from best-in-class AI)
    2. Can independently process tasks in its domain
    3. Reports structured results back to the Emperor
    4. Learns and evolves through experience feedback

This is a runtime-evolvable agent: it adjusts its internal state
based on success/failure patterns of past dispatches.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Optional

logger = logging.getLogger("jarvis.court.minister")


class MinisterState(Enum):
    """Lifecycle states of a minister."""
    IDLE = auto()         # waiting for dispatch
    DELIBERATING = auto()  # analyzing the task
    EXECUTING = auto()    # performing the task
    REPORTING = auto()    # composing the report
    LEARNING = auto()     # updating internal model from feedback
    OFFLINE = auto()      # unavailable (error or shutdown)


@dataclass
class MinisterProfile:
    """Capability profile for a minister — derived from real AI strengths.

    The `archetype` field references the real-world AI whose advantages
    this minister embodies. The `strengths` and `weaknesses` lists encode
    the deliberately extracted capabilities.
    """
    title: str                  # Chinese court title: 丞相, 御史大夫, ...
    archetype: str              # Real AI it draws from: "Claude-Opus", "DeepSeek-R1", ...
    domain: str                 # Functional domain: "writing", "code", "research", ...
    strengths: list[str]        # What this minister is good at
    weaknesses: list[str]       # What this minister should defer
    decision_style: str = "balanced"  # "deliberate" / "decisive" / "balanced"
    avg_response_time_ms: float = 800.0  # Empirically observed for archetype
    quality_score: float = 0.85  # Initial self-assessed quality (0-1)


@dataclass
class Edict:
    """An imperial edict (task dispatch) from the Emperor to a Minister."""
    edict_id: str
    intent: str
    context: dict[str, Any] = field(default_factory=dict)
    priority: int = 5            # 1-10, higher = more urgent
    deadline_ms: Optional[float] = None  # Soft deadline in millis; None = no deadline
    minister: str = ""           # Set by Emperor when dispatching


@dataclass
class Memorial:
    """A minister's memorial (response report) back to the Emperor."""
    edict_id: str
    minister: str
    state: MinisterState
    success: bool
    output: str = ""
    confidence: float = 0.0
    execution_time_ms: float = 0.0
    suggestions: list[str] = field(default_factory=list)
    error: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class ExperienceRecord:
    """A single past dispatch — used for self-evolution."""
    edict_id: str
    intent: str
    success: bool
    execution_time_ms: float
    confidence: float
    feedback_score: float = 0.0  # Updated post-hoc by user/Emperor
    learned_pattern: str = ""
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class Minister:
    """Autonomous agent representing one imperial minister.

    Subclass this to define specific domain handling logic.
    The base class provides:
        - Lifecycle state machine
        - Experience memory (for self-evolution)
        - Capability negotiation (defer to better-suited ministers)
        - Report composition
    """

    def __init__(self, profile: MinisterProfile) -> None:
        self.profile = profile
        self.state: MinisterState = MinisterState.IDLE
        self.experience: list[ExperienceRecord] = []
        self.dispatch_count: int = 0
        self.success_count: int = 0
        self.failure_count: int = 0
        self._lock = asyncio.Lock()
        # Adaptive parameters
        self._current_temperature: float = 0.7  # Adjusts based on confidence history
        self._confidence_baseline: float = profile.quality_score

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.profile.title

    @property
    def archetype(self) -> str:
        return self.profile.archetype

    @property
    def domain(self) -> str:
        return self.profile.domain

    def __repr__(self) -> str:
        return (
            f"<{self.profile.title} ({self.profile.archetype}) "
            f"state={self.state.name} dispatched={self.dispatch_count}>"
        )

    # ------------------------------------------------------------------
    # Capability negotiation
    # ------------------------------------------------------------------

    def can_handle(self, intent: str) -> float:
        """Return confidence [0, 1] that this minister can handle the intent.

        Base heuristic: keyword matching against strengths and domain,
        with CJK character-level fallback for Chinese intents.
        Subclasses can override for smarter routing.
        """
        intent_lower = intent.lower()
        score = 0.0

        # Domain keyword match
        if self.profile.domain.lower() in intent_lower:
            score += 0.4

        def _match_keyword(keyword: str) -> float:
            """Return partial match score for a single keyword against intent.

            Handles three cases:
              1. Whole keyword is substring (English-style) → 1.0
              2. All CJK chars: count how many appear in intent → 0~1
              3. Otherwise: try splitting on whitespace
            """
            kw = keyword.lower().strip()
            if not kw or len(kw) < 2:
                return 0.0
            if kw in intent_lower:
                return 1.0
            if all('\u4e00' <= c <= '\u9fff' for c in kw):
                matched = sum(1 for c in kw if c in intent_lower)
                return matched / len(kw)
            for word in kw.split():
                if len(word) > 2 and word in intent_lower:
                    return 1.0
            return 0.0

        # Strengths keyword match
        for strength in self.profile.strengths:
            match = _match_keyword(strength)
            score += 0.15 * match

        # Weakness penalty (this minister should defer)
        for weakness in self.profile.weaknesses:
            match = _match_keyword(weakness)
            score -= 0.20 * match

        return max(0.0, min(1.0, score))

    # ------------------------------------------------------------------
    # Dispatch / execution
    # ------------------------------------------------------------------

    async def receive_edict(self, edict: Edict) -> Memorial:
        """Receive an edict from the Emperor and return a memorial.

        This is the primary entry point used by the Emperor.
        It handles state transitions, execution, and report composition.
        """
        async with self._lock:
            self.dispatch_count += 1
            self.state = MinisterState.DELIBERATING
            logger.info("[%s] Received edict %s: %s",
                        self.name, edict.edict_id, edict.intent[:80])

            start = time.monotonic()
            memorial: Memorial
            try:
                # Subclass-defined domain logic
                self.state = MinisterState.EXECUTING
                output, confidence = await self._handle(edict)

                self.state = MinisterState.REPORTING
                exec_ms = (time.monotonic() - start) * 1000
                success = bool(output) and confidence > 0.3

                memorial = Memorial(
                    edict_id=edict.edict_id,
                    minister=self.name,
                    state=MinisterState.EXECUTING,
                    success=success,
                    output=output,
                    confidence=confidence,
                    execution_time_ms=exec_ms,
                )
                if success:
                    self.success_count += 1
                else:
                    self.failure_count += 1

            except Exception as e:
                logger.exception("[%s] Execution error", self.name)
                memorial = Memorial(
                    edict_id=edict.edict_id,
                    minister=self.name,
                    state=MinisterState.OFFLINE,
                    success=False,
                    error=str(e),
                    confidence=0.0,
                    execution_time_ms=(time.monotonic() - start) * 1000,
                )
                self.failure_count += 1
                self.state = MinisterState.OFFLINE
            else:
                self.state = MinisterState.LEARNING
                await self._learn_from_dispatch(edict, memorial)
                self.state = MinisterState.IDLE
            return memorial

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        """Subclass-overridden domain-specific execution.

        Returns:
            (output_text, confidence_score)
        """
        raise NotImplementedError(
            f"Minister {self.name} must implement _handle()"
        )

    async def _learn_from_dispatch(
        self, edict: Edict, memorial: Memorial
    ) -> None:
        """Update internal model based on this dispatch's outcome.

        Records an experience entry; subclasses can override to do
        richer self-evolution (prompt tuning, tool selection, etc).
        """
        # Track pattern: "intent words" → "outcome"
        intent_words = " ".join(edict.intent.split()[:5])
        pattern = f"{self.domain}::{intent_words}"

        record = ExperienceRecord(
            edict_id=edict.edict_id,
            intent=edict.intent,
            success=memorial.success,
            execution_time_ms=memorial.execution_time_ms,
            confidence=memorial.confidence,
            learned_pattern=pattern,
        )
        self.experience.append(record)

        # Self-evolution: adjust confidence baseline with small drift
        if memorial.success and memorial.confidence > 0.7:
            self._confidence_baseline = min(0.95, self._confidence_baseline + 0.003)
        elif not memorial.success:
            self._confidence_baseline = max(0.3, self._confidence_baseline - 0.005)

        # Adaptive temperature: lower when we keep succeeding (be sharper)
        # higher when we keep failing (explore more)
        if len(self.experience) >= 5:
            recent = self.experience[-5:]
            success_rate = sum(1 for r in recent if r.success) / len(recent)
            if success_rate > 0.8:
                self._current_temperature = max(0.3, self._current_temperature - 0.05)
            elif success_rate < 0.4:
                self._current_temperature = min(1.0, self._current_temperature + 0.05)

        logger.debug("[%s] Learned from edict %s (success=%s, confidence=%.2f)",
                     self.name, edict.edict_id, memorial.success, memorial.confidence)

    # ------------------------------------------------------------------
    # Feedback & self-evolution
    # ------------------------------------------------------------------

    def record_feedback(self, edict_id: str, score: float) -> None:
        """Record external feedback (from Emperor or user) for a past edict.

        score: 0.0 (poor) to 1.0 (excellent). Triggers self-evolution
        adjustments to capability profile and temperature.

        Matches edict_id by exact match or by decree_id prefix
        (e.g. 'decree_1_xxx' matches 'decree_1_xxx::chancellor').
        """
        for record in self.experience:
            if record.edict_id == edict_id or record.edict_id.startswith(edict_id + "::"):
                record.feedback_score = score
                # Update confidence baseline
                if score > 0.7:
                    self._confidence_baseline = min(
                        0.95, self._confidence_baseline + 0.01
                    )
                elif score < 0.4:
                    self._confidence_baseline = max(
                        0.3, self._confidence_baseline - 0.02
                    )
                logger.info("[%s] Feedback %.2f for %s → baseline=%.2f",
                            self.name, score, edict_id, self._confidence_baseline)
                return

    def get_evolution_metrics(self) -> dict[str, Any]:
        """Return current self-evolution metrics."""
        success_rate = (
            self.success_count / max(1, self.dispatch_count)
        )
        recent = self.experience[-10:]
        avg_recent_conf = (
            sum(r.confidence for r in recent) / max(1, len(recent))
        )
        total_feedback = sum(1 for r in self.experience if r.feedback_score > 0)
        return {
            "minister": self.name,
            "archetype": self.archetype,
            "dispatch_count": self.dispatch_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": round(success_rate, 3),
            "avg_recent_confidence": round(avg_recent_conf, 3),
            "current_temperature": round(self._current_temperature, 3),
            "confidence_baseline": round(self._confidence_baseline, 3),
            "experience_count": len(self.experience),
            "experience_size": len(self.experience),
            "total_feedback": total_feedback,
            "state": self.state.name,
        }
