"""
MeritBoard (功勋榜) — public performance ranking and merit-based selection.

Inspired by Qin dynasty's 二十等爵制 (20-rank meritocracy), where titles,
land, and authority were earned exclusively through battlefield performance.

The MeritBoard serves as the court's competitive backbone:
    1. Tracks every minister's dispatch outcomes — wins, losses, quality
    2. Computes composite merit scores with recency weighting
    3. Ranks ministers publicly — visible to all, creating competitive pressure
    4. Identifies bottom performers for SurvivalMechanism elimination
    5. Provides merit-weighted selection for Emperor's court sessions
    6. Drives ShadowCabinet promotion/demotion decisions

Scoring formula (军功积分制):
    merit = success_rate × 40 + avg_confidence × 30 + feedback_avg × 20 + recency_bonus × 10
    recency: exponential decay, half-life = 20 dispatches
    floor: no minister goes below 10 (人情底线)
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger("jarvis.court.merit_board")


class MeritRank(Enum):
    """Merit tiers — 爵位等级.

    Patterned after the Qin 20-rank system, simplified to 5 tiers
    for clarity. Higher tiers get priority in court sessions and
    weighted voting.
    """
    COMMONER = auto()       # 庶民 — new or probation, rank < 20
    KNIGHT = auto()         # 公士 — reliable contributor, rank 20-39
    OFFICER = auto()        # 大夫 — strong performer, rank 40-59
    MINISTER = auto()       # 卿 — elite, rank 60-79
    GRANDEE = auto()        # 彻侯 — top-tier, rank 80-100

    @classmethod
    def from_score(cls, score: float) -> "MeritRank":
        if score >= 80:
            return cls.GRANDEE
        if score >= 60:
            return cls.MINISTER
        if score >= 40:
            return cls.OFFICER
        if score >= 20:
            return cls.KNIGHT
        return cls.COMMONER


@dataclass
class DispatchEntry:
    """A single dispatch recorded in the merit ledger."""
    edict_id: str
    minister: str
    intent: str
    success: bool
    confidence: float
    execution_time_ms: float
    feedback_score: float = 0.0  # post-hoc feedback from Emperor/user
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class MeritReport:
    """Snapshot of a minister's current merit standing."""
    minister: str
    merit_score: float
    rank: MeritRank
    court_position: int          # 1 = best
    total_dispatches: int
    success_rate: float
    avg_confidence: float
    avg_feedback: float
    streak: int                  # positive = win streak, negative = loss streak
    recent_trend: str            # "rising" / "stable" / "falling"
    probation_count: int = 0     # how many times they've been flagged
    eliminated: bool = False


class MeritBoard:
    """The court's central performance ledger.

    Usage:
        board = MeritBoard()
        board.record_dispatch("丞相", edict_id="e1", success=True, confidence=0.85)
        board.record_dispatch("工部尚书", edict_id="e2", success=False, confidence=0.2)

        ranking = board.get_ranking()
        # ranking[0] → best minister's MeritReport

        bottom = board.get_bottom_n(2)  # → bottom 2 for SurvivalMechanism
    """

    # Scoring weights (must sum to 1.0)
    WEIGHT_SUCCESS_RATE = 0.40
    WEIGHT_CONFIDENCE = 0.30
    WEIGHT_FEEDBACK = 0.20
    WEIGHT_RECENCY = 0.10

    # Recency decay: half-life in number of dispatches
    RECENCY_HALF_LIFE = 20

    # Bottom performers: what fraction enters probation zone
    PROBATION_FRACTION = 0.25

    def __init__(self) -> None:
        self._ledger: dict[str, list[DispatchEntry]] = defaultdict(list)
        self._probation: dict[str, int] = defaultdict(int)  # minister → consecutive failures
        self._eliminated: set[str] = set()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_dispatch(
        self,
        minister: str,
        edict_id: str,
        intent: str,
        success: bool,
        confidence: float,
        execution_time_ms: float = 0.0,
    ) -> None:
        """Record a dispatch outcome into the merit ledger."""
        entry = DispatchEntry(
            edict_id=edict_id,
            minister=minister,
            intent=intent,
            success=success,
            confidence=confidence,
            execution_time_ms=execution_time_ms,
        )
        self._ledger[minister].append(entry)

    def record_feedback(
        self, minister: str, edict_id: str, score: float
    ) -> bool:
        """Record external feedback for a past dispatch.

        Returns True if the entry was found and updated.
        """
        for entry in reversed(self._ledger.get(minister, [])):
            if entry.edict_id == edict_id:
                entry.feedback_score = max(0.0, min(1.0, score))
                return True
            # Match by prefix (decree_id::minister pattern)
            if edict_id.startswith(
                entry.edict_id.rsplit("::", 1)[0]
            ):
                entry.feedback_score = max(0.0, min(1.0, score))
                return True
        return False

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def compute_merit(self, minister: str) -> float:
        """Compute the composite merit score for a minister.

        Formula:
            merit = success_rate × 40 + avg_confidence × 30
                  + feedback_avg × 20 + recency_bonus × 10

        capped to [0, 100].
        """
        if minister in self._eliminated:
            return 0.0

        entries = self._ledger.get(minister, [])
        if not entries:
            return 10.0  # new ministers start at baseline

        total = len(entries)
        successes = sum(1 for e in entries if e.success)
        success_rate = successes / total

        avg_confidence = sum(e.confidence for e in entries) / total

        feedbacks = [e.feedback_score for e in entries if e.feedback_score > 0]
        avg_feedback = sum(feedbacks) / len(feedbacks) if feedbacks else 0.5

        recency_bonus = self._compute_recency_bonus(entries)

        raw = (
            success_rate * 100 * self.WEIGHT_SUCCESS_RATE
            + avg_confidence * 100 * self.WEIGHT_CONFIDENCE
            + avg_feedback * 100 * self.WEIGHT_FEEDBACK
            + recency_bonus * 100 * self.WEIGHT_RECENCY
        )

        # Floor: even the worst performer gets 10 (人情底线)
        return max(10.0, min(100.0, raw))

    def _compute_recency_bonus(
        self, entries: list[DispatchEntry]
    ) -> float:
        """Compute recency bonus using exponential decay.

        Recent successes count more. Half-life = RECENCY_HALF_LIFE dispatches.
        """
        if not entries:
            return 0.0

        n = len(entries)
        decay = math.log(2) / self.RECENCY_HALF_LIFE
        weighted_sum = 0.0
        total_weight = 0.0

        for i, entry in enumerate(entries):
            age = n - 1 - i  # 0 = most recent
            weight = math.exp(-decay * age)
            value = 1.0 if entry.success else 0.0
            weighted_sum += value * weight
            total_weight += weight

        return weighted_sum / max(0.001, total_weight)

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def get_ranking(self) -> list[MeritReport]:
        """Return all ministers ranked by merit, best first."""
        reports = []
        for minister in self._ledger:
            reports.append(self._build_report(minister))

        # Sort descending by merit
        reports.sort(key=lambda r: r.merit_score, reverse=True)

        # Assign positions
        for i, report in enumerate(reports):
            report.court_position = i + 1

        return reports

    def get_top_n(self, n: int) -> list[MeritReport]:
        """Return top N ministers by merit."""
        ranking = self.get_ranking()
        return ranking[:n]

    def get_bottom_n(self, n: int) -> list[MeritReport]:
        """Return bottom N ministers by merit."""
        ranking = self.get_ranking()
        return ranking[-n:] if n <= len(ranking) else ranking

    def get_probation_candidates(self) -> list[str]:
        """Identify ministers who should enter probation.

        Criteria:
        - Bottom PROBATION_FRACTION (25%) of the ranking
        - OR 3+ consecutive failures
        """
        ranking = self.get_ranking()
        if not ranking:
            return []

        cutoff = max(1, int(len(ranking) * self.PROBATION_FRACTION))
        bottom_reports = ranking[-cutoff:]

        candidates = []
        for report in bottom_reports:
            # Must not already be eliminated
            if report.eliminated:
                continue
            # Must be bottom-tier
            if report.merit_score < 30:
                candidates.append(report.minister)

        # Also catch anyone on a 3+ loss streak
        for minister, entries in self._ledger.items():
            if minister in self._eliminated:
                continue
            recent = entries[-5:]
            if len(recent) >= 3:
                consecutive_losses = 0
                for e in reversed(recent):
                    if not e.success:
                        consecutive_losses += 1
                    else:
                        break
                if consecutive_losses >= 3 and minister not in candidates:
                    candidates.append(minister)

        return candidates

    def mark_eliminated(self, minister: str) -> None:
        """Permanently remove a minister from the merit board."""
        self._eliminated.add(minister)
        logger.warning("[MeritBoard] %s 已淘汰 (末位淘汰)", minister)

    def is_eliminated(self, minister: str) -> bool:
        return minister in self._eliminated

    # ------------------------------------------------------------------
    # Leaderboard
    # ------------------------------------------------------------------

    def get_leaderboard(self) -> dict[str, Any]:
        """Return a structured leaderboard for display."""
        ranking = self.get_ranking()
        return {
            "total_ministers": len(ranking),
            "eliminated": list(self._eliminated),
            "rankings": [
                {
                    "position": r.court_position,
                    "minister": r.minister,
                    "rank": r.rank.name,
                    "merit_score": round(r.merit_score, 1),
                    "success_rate": round(r.success_rate, 3),
                    "avg_confidence": round(r.avg_confidence, 3),
                    "streak": r.streak,
                    "trend": r.recent_trend,
                }
                for r in ranking
            ],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_report(self, minister: str) -> MeritReport:
        """Build a MeritReport for a single minister."""
        entries = self._ledger.get(minister, [])
        total = len(entries)
        if total == 0:
            return MeritReport(
                minister=minister,
                merit_score=10.0,
                rank=MeritRank.COMMONER,
                court_position=0,
                total_dispatches=0,
                success_rate=0.0,
                avg_confidence=0.0,
                avg_feedback=0.0,
                streak=0,
                recent_trend="stable",
                eliminated=minister in self._eliminated,
            )

        successes = sum(1 for e in entries if e.success)
        success_rate = successes / total
        avg_confidence = sum(e.confidence for e in entries) / total
        feedbacks = [e.feedback_score for e in entries if e.feedback_score > 0]
        avg_feedback = sum(feedbacks) / len(feedbacks) if feedbacks else 0.0

        # Streak
        streak = 0
        for e in reversed(entries):
            if e.success:
                if streak >= 0:
                    streak += 1
                else:
                    break
            else:
                if streak <= 0:
                    streak -= 1
                else:
                    break

        # Trend: compare first half vs second half
        half = max(1, total // 2)
        first_half_rate = (
            sum(1 for e in entries[:half] if e.success) / half
        )
        second_half_rate = (
            sum(1 for e in entries[-half:] if e.success) / half
        )
        delta = second_half_rate - first_half_rate
        if delta > 0.1:
            trend = "rising"
        elif delta < -0.1:
            trend = "falling"
        else:
            trend = "stable"

        merit_score = self.compute_merit(minister)

        return MeritReport(
            minister=minister,
            merit_score=merit_score,
            rank=MeritRank.from_score(merit_score),
            court_position=0,  # filled by get_ranking
            total_dispatches=total,
            success_rate=success_rate,
            avg_confidence=avg_confidence,
            avg_feedback=avg_feedback,
            streak=streak,
            recent_trend=trend,
            eliminated=minister in self._eliminated,
        )

    def success_rate(self) -> float:
        """Aggregate success rate across all dispatch records (0.0-1.0).

        Returns 0.0 when no dispatches have been recorded.
        """
        total = 0
        successes = 0
        for entries in self._ledger.values():
            for e in entries:
                total += 1
                if e.success:
                    successes += 1
        if total == 0:
            return 0.0
        return successes / total
