"""
SlidingMeritBoard (滑动功勋榜) — windowed merit evaluation.

While MeritBoard accumulates all historical dispatches, SlidingMeritBoard
computes merit scores using only the most recent N entries per minister.
This makes the system far more responsive to recent performance shifts:

    1. A minister who was great 100 cycles ago but terrible recently
       will see their merit drop rapidly — not coast on old glory.
    2. A new minister who hits a hot streak gets immediate recognition.
    3. Time-decay mode gives smooth exponential weighting as alternative.

Configuration:
    window_size=50     → last 50 dispatches
    decay_mode=False   → hard window cutoff
    decay_mode=True    → exponential decay (half-life = window_size)

Architecture: wraps MeritBoard, does NOT modify its internal ledger.
All reads go through SlidingMeritBoard; writes still go to MeritBoard.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

from jarvis.court.merit_board import (
    DispatchEntry,
    MeritBoard,
    MeritRank,
    MeritReport,
)

logger = logging.getLogger("jarvis.court.sliding_merit")


class WindowMode(Enum):
    """Sliding window strategy."""
    HARD_CUTOFF = auto()   # Only last window_size entries
    EXP_DECAY = auto()      # Exponential recency weighting


@dataclass
class SlidingMeritReport(MeritReport):
    """Extended MeritReport with windowed metrics."""
    window_size: int = 0
    entries_in_window: int = 0
    windowed_merit: float = 0.0
    full_merit: float = 0.0          # MeritBoard's cumulative score
    merit_delta: float = 0.0         # windowed - full (positive = improving)
    volatility: float = 0.0          # std-dev of recent success rate
    momentum: float = 0.0            # rate of change per dispatch


class SlidingMeritBoard:
    """Windowed merit evaluation layer wrapping MeritBoard.

    Key design decisions:
        - Does NOT modify MeritBoard._ledger — reads only.
        - compute_merit() returns windowed score instead of cumulative.
        - get_ranking() sorts by windowed merit.
        - get_bottom_n() / get_probation_candidates() use windowed merit.
        - All original MeritBoard fields remain accessible via self.board.
        - Default window_size=50 balances responsiveness vs stability.
        - EXP_DECAY mode smoothly weights older entries without hard cutoff.

    Usage:
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=50)
        sliding.record_dispatch("丞相", "e1", "advice", True, 0.85)
        score = sliding.compute_merit("丞相")  # windowed score
        ranking = sliding.get_ranking()        # sorted by windowed score
    """

    # ── Class constants ─────────────────────────────────────────────

    # Same scoring weights as MeritBoard
    WEIGHT_SUCCESS_RATE = 0.40
    WEIGHT_CONFIDENCE = 0.30
    WEIGHT_FEEDBACK = 0.20
    WEIGHT_RECENCY = 0.10

    # Default window size: 50 dispatches
    DEFAULT_WINDOW_SIZE = 50

    # Half-life for exponential decay (as fraction of window)
    DECAY_HALF_LIFE_FRACTION = 0.4

    def __init__(
        self,
        board: MeritBoard,
        window_size: int = DEFAULT_WINDOW_SIZE,
        mode: WindowMode = WindowMode.HARD_CUTOFF,
    ) -> None:
        """Wrap a MeritBoard with sliding window logic.

        Args:
            board: Underlying MeritBoard instance.
            window_size: Number of most recent dispatches to consider.
            mode: HARD_CUTOFF or EXP_DECAY.
        """
        self.board = board
        self.window_size = max(3, min(500, window_size))
        self.mode = mode
        self._decay_half_life = max(
            5, int(self.window_size * self.DECAY_HALF_LIFE_FRACTION)
        )

    # ── Properties: transparent access to underlying board internals ─

    @property
    def _ledger(self) -> dict:
        """Passthrough to underlying MeritBoard._ledger.

        Enables SurvivalMechanism to iterate dispatch entries for
        confidence baseline tracking without knowing which board type
        is backing the merit evaluation.
        """
        return self.board._ledger

    @property
    def _eliminated(self) -> set:
        """Passthrough to underlying MeritBoard._eliminated."""
        return self.board._eliminated

    # ── Passthrough: writes go to underlying board ──────────────────

    def record_dispatch(
        self,
        minister: str,
        edict_id: str,
        intent: str,
        success: bool,
        confidence: float,
        execution_time_ms: float = 0.0,
    ) -> None:
        """Record dispatch — delegates to underlying MeritBoard."""
        self.board.record_dispatch(
            minister, edict_id, intent, success, confidence, execution_time_ms
        )

    def record_feedback(self, minister: str, edict_id: str, score: float) -> bool:
        """Record feedback — delegates to underlying MeritBoard."""
        return self.board.record_feedback(minister, edict_id, score)

    def mark_eliminated(self, minister: str) -> None:
        self.board.mark_eliminated(minister)

    def is_eliminated(self, minister: str) -> bool:
        return self.board.is_eliminated(minister)

    # ── Window helpers ──────────────────────────────────────────────

    def _windowed_entries(self, minister: str) -> list[DispatchEntry]:
        """Return only the last window_size entries for a minister."""
        entries = self.board._ledger.get(minister, [])
        if len(entries) <= self.window_size:
            return entries
        return entries[-self.window_size:]

    # ── Scoring (windowed) ──────────────────────────────────────────

    def compute_merit(self, minister: str) -> float:
        """Compute windowed merit score.

        HARD_CUTOFF: only last window_size entries.
        EXP_DECAY: all entries with exponential recency weights.
        """
        if minister in self.board._eliminated:
            return 0.0

        entries = self.board._ledger.get(minister, [])
        if not entries:
            return 10.0  # baseline

        if self.mode == WindowMode.EXP_DECAY:
            return self._compute_decay_merit(entries, minister)
        else:
            return self._compute_hard_cutoff_merit(minister)

    def _compute_hard_cutoff_merit(self, minister: str) -> float:
        """Merit from last window_size entries only."""
        entries = self._windowed_entries(minister)
        total = len(entries)
        if total == 0:
            return 10.0

        successes = sum(1 for e in entries if e.success)
        success_rate = successes / total
        avg_confidence = sum(e.confidence for e in entries) / total
        feedbacks = [e.feedback_score for e in entries if e.feedback_score > 0]
        avg_feedback = sum(feedbacks) / len(feedbacks) if feedbacks else 0.5

        raw = (
            success_rate * 100 * self.WEIGHT_SUCCESS_RATE
            + avg_confidence * 100 * self.WEIGHT_CONFIDENCE
            + avg_feedback * 100 * self.WEIGHT_FEEDBACK
            + 0.0  # no separate recency bonus in hard cutoff
        )

        return max(10.0, min(100.0, raw))

    def _compute_decay_merit(
        self, entries: list[DispatchEntry], minister: str
    ) -> float:
        """Merit with exponential recency decay across ALL entries."""
        n = len(entries)
        decay = math.log(2) / self._decay_half_life

        weighted_success = 0.0
        weighted_confidence = 0.0
        weighted_feedback = 0.0
        total_weight = 0.0
        feedback_weight = 0.0

        for i, entry in enumerate(entries):
            age = n - 1 - i  # 0 = most recent
            weight = math.exp(-decay * age)

            weighted_success += (1.0 if entry.success else 0.0) * weight
            weighted_confidence += entry.confidence * weight
            total_weight += weight

            if entry.feedback_score > 0:
                weighted_feedback += entry.feedback_score * weight
                feedback_weight += weight

        if total_weight == 0:
            return 10.0

        success_rate = weighted_success / total_weight
        avg_confidence = weighted_confidence / total_weight
        avg_feedback = (
            weighted_feedback / feedback_weight if feedback_weight > 0 else 0.5
        )

        raw = (
            success_rate * 100 * self.WEIGHT_SUCCESS_RATE
            + avg_confidence * 100 * self.WEIGHT_CONFIDENCE
            + avg_feedback * 100 * self.WEIGHT_FEEDBACK
            + 0.0
        )

        return max(10.0, min(100.0, raw))

    # ── Full merit (delegated) ──────────────────────────────────────

    def compute_full_merit(self, minister: str) -> float:
        """Delegate to underlying MeritBoard for cumulative score."""
        return self.board.compute_merit(minister)

    # ── Ranking (windowed) ──────────────────────────────────────────

    def get_ranking(self) -> list[SlidingMeritReport]:
        """Return all ministers ranked by windowed merit."""
        reports = []
        for minister in self.board._ledger:
            reports.append(self._build_report(minister))

        reports.sort(key=lambda r: r.windowed_merit, reverse=True)
        for i, r in enumerate(reports):
            r.court_position = i + 1

        return reports

    def get_top_n(self, n: int) -> list[SlidingMeritReport]:
        ranking = self.get_ranking()
        return ranking[:n]

    def get_bottom_n(self, n: int) -> list[SlidingMeritReport]:
        ranking = self.get_ranking()
        return ranking[-n:] if n <= len(ranking) else ranking

    def get_probation_candidates(self) -> list[str]:
        """Probation: bottom 25% by windowed merit + 3+ consecutive failures."""
        ranking = self.get_ranking()
        if not ranking:
            return []

        cutoff = max(1, int(len(ranking) * MeritBoard.PROBATION_FRACTION))
        bottom_reports = ranking[-cutoff:]

        candidates = []
        for report in bottom_reports:
            if report.eliminated:
                continue
            if report.windowed_merit < 30:
                candidates.append(report.minister)

        # 3+ loss streak
        for minister, entries in self.board._ledger.items():
            if minister in self.board._eliminated:
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

    # ── Leaderboard ─────────────────────────────────────────────────

    def get_leaderboard(self) -> dict[str, Any]:
        ranking = self.get_ranking()
        return {
            "total_ministers": len(ranking),
            "window_size": self.window_size,
            "mode": self.mode.name,
            "eliminated": list(self.board._eliminated),
            "rankings": [
                {
                    "position": r.court_position,
                    "minister": r.minister,
                    "rank": r.rank.name,
                    "windowed_merit": round(r.windowed_merit, 1),
                    "full_merit": round(r.full_merit, 1),
                    "merit_delta": round(r.merit_delta, 2),
                    "success_rate": round(r.success_rate, 3),
                    "streak": r.streak,
                    "trend": r.recent_trend,
                    "momentum": round(r.momentum, 3),
                    "entries_in_window": r.entries_in_window,
                }
                for r in ranking
            ],
        }

    # ── Volatility & momentum ───────────────────────────────────────

    def compute_volatility(self, minister: str) -> float:
        """Std-dev of binary success over the window (0 or 1 per dispatch)."""
        entries = self._windowed_entries(minister)
        if len(entries) < 5:
            return 0.0
        outcomes = [1.0 if e.success else 0.0 for e in entries]
        mean = sum(outcomes) / len(outcomes)
        variance = sum((x - mean) ** 2 for x in outcomes) / len(outcomes)
        return math.sqrt(variance)

    def compute_momentum(self, minister: str) -> float:
        """Linear regression slope of success over window entries.

        Positive = improving, negative = declining.
        """
        entries = self._windowed_entries(minister)
        if len(entries) < 5:
            return 0.0

        n = len(entries)
        x_mean = (n - 1) / 2.0
        outcomes = [1.0 if e.success else 0.0 for e in entries]
        y_mean = sum(outcomes) / n

        num = sum((i - x_mean) * (outcomes[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))

        if abs(den) < 1e-9:
            return 0.0
        return num / den

    # ── Report builder ──────────────────────────────────────────────

    def _build_report(self, minister: str) -> SlidingMeritReport:
        """Build a SlidingMeritReport with both windowed and full metrics."""
        all_entries = self.board._ledger.get(minister, [])
        w_entries = self._windowed_entries(minister)
        total = len(all_entries)
        w_total = len(w_entries)

        if total == 0:
            return SlidingMeritReport(
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
                eliminated=minister in self.board._eliminated,
                window_size=self.window_size,
                entries_in_window=0,
                windowed_merit=10.0,
                full_merit=10.0,
                merit_delta=0.0,
                volatility=0.0,
                momentum=0.0,
            )

        # Windowed success rate
        w_successes = sum(1 for e in w_entries if e.success)
        w_success_rate = w_successes / w_total if w_total > 0 else 0.0

        # Full metrics (for compatibility)
        successes = sum(1 for e in all_entries if e.success)
        success_rate = successes / total
        avg_confidence = sum(e.confidence for e in all_entries) / total
        feedbacks = [e.feedback_score for e in all_entries if e.feedback_score > 0]
        avg_feedback = sum(feedbacks) / len(feedbacks) if feedbacks else 0.0

        # Streak (from all entries)
        streak = 0
        for e in reversed(all_entries):
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

        # Trend: window first-half vs second-half
        w_half = max(1, w_total // 2)
        first_half_rate = (
            sum(1 for e in w_entries[:w_half] if e.success) / w_half
            if w_half > 0 else 0.0
        )
        second_half_rate = (
            sum(1 for e in w_entries[-w_half:] if e.success) / w_half
            if w_half > 0 else 0.0
        )
        delta_trend = second_half_rate - first_half_rate
        if delta_trend > 0.15:
            trend = "rising"
        elif delta_trend < -0.15:
            trend = "falling"
        else:
            trend = "stable"

        windowed_merit = self.compute_merit(minister)
        full_merit = self.compute_full_merit(minister)
        merit_delta = windowed_merit - full_merit
        volatility = self.compute_volatility(minister)
        momentum = self.compute_momentum(minister)

        rank = MeritRank.from_score(windowed_merit)

        return SlidingMeritReport(
            minister=minister,
            merit_score=windowed_merit,
            rank=rank,
            court_position=0,
            total_dispatches=total,
            success_rate=success_rate,
            avg_confidence=avg_confidence,
            avg_feedback=avg_feedback,
            streak=streak,
            recent_trend=trend,
            eliminated=minister in self.board._eliminated,
            window_size=self.window_size,
            entries_in_window=w_total,
            windowed_merit=windowed_merit,
            full_merit=full_merit,
            merit_delta=merit_delta,
            volatility=volatility,
            momentum=momentum,
        )
