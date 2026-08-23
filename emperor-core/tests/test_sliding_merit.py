"""
SlidingMeritBoard tests — windowed merit evaluation.

Covers:
    - WindowMode enum: HARD_CUTOFF vs EXP_DECAY
    - Basic windowed merit: only last N entries count
    - Full merit passthrough via compute_full_merit()
    - SlidingMeritReport extended fields: windowed_merit, full_merit, delta, volatility, momentum
    - Ranking by windowed merit (not cumulative)
    - Probation candidates based on windowed scores
    - Volatility computation (std-dev of success)
    - Momentum computation (linear regression slope)
    - Edge cases: empty, single entry, window larger than history
    - EXP_DECAY mode: exponential weighting
    - Leaderboard with windowed metrics
    - Integration: record_dispatch via SlidingMeritBoard
    - Compatibility: MeritBoard field access via .board
"""

import math
import pytest

from jarvis.court.merit_board import MeritBoard
from jarvis.court.sliding_merit import (
    SlidingMeritBoard,
    SlidingMeritReport,
    WindowMode,
)

# ── Helpers ────────────────────────────────────────────────────────


def _record_n(sliding: SlidingMeritBoard, minister: str, outcomes: list[bool]) -> None:
    """Record a series of dispatch outcomes for a minister."""
    for i, success in enumerate(outcomes):
        sliding.record_dispatch(
            minister,
            f"e{i}",
            "test",
            success,
            confidence=0.7 if success else 0.3,
        )


# ── WindowMode enum ─────────────────────────────────────────────────


class TestWindowMode:
    def test_modes_exist(self):
        assert WindowMode.HARD_CUTOFF is not None
        assert WindowMode.EXP_DECAY is not None

    def test_modes_unique(self):
        assert WindowMode.HARD_CUTOFF.value != WindowMode.EXP_DECAY.value


# ── Construction ────────────────────────────────────────────────────


class TestConstruction:
    def test_default_window_size(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        assert sliding.window_size == 50
        assert sliding.mode == WindowMode.HARD_CUTOFF

    def test_custom_window_size(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=20)
        assert sliding.window_size == 20

    def test_window_size_clamped(self):
        board = MeritBoard()
        sliding2 = SlidingMeritBoard(board, window_size=9999)
        assert sliding2.window_size == 500  # clamped to max

    def test_decay_mode(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, mode=WindowMode.EXP_DECAY)
        assert sliding.mode == WindowMode.EXP_DECAY

    def test_underlying_board_accessible(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        assert sliding.board is board


# ── Windowed entries ────────────────────────────────────────────────


class TestWindowedEntries:
    def test_window_smaller_than_history(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 10)
        assert len(sliding._windowed_entries("A")) == 5

    def test_window_larger_than_history(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=50)
        _record_n(sliding, "A", [True] * 10)
        assert len(sliding._windowed_entries("A")) == 10

    def test_window_exact_match(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=10)
        _record_n(sliding, "A", [True] * 10)
        assert len(sliding._windowed_entries("A")) == 10

    def test_window_returns_most_recent(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=3)
        _record_n(sliding, "A", [False, False, False, True, True, True])
        entries = sliding._windowed_entries("A")
        assert all(e.success for e in entries)

    def test_empty_minister(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        assert sliding._windowed_entries("nonexistent") == []


# ── compute_merit (hard cutoff) ─────────────────────────────────────


class TestComputeMeritHardCutoff:
    """HARD_CUTOFF: only last N entries."""

    def test_all_successes_window(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 5)
        score = sliding.compute_merit("A")
        assert score > 70  # high confidence + all wins

    def test_all_failures_window(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [False] * 5)
        score = sliding.compute_merit("A")
        assert score < 30

    def test_mixed_window(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=4)
        _record_n(sliding, "A", [True, True, False, False])
        score = sliding.compute_merit("A")
        assert 25 < score < 60

    def test_window_ignores_old_history(self):
        """Old successes outside window should not inflate merit."""
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        # 15 old wins, 5 recent losses
        _record_n(sliding, "A", [True] * 15 + [False] * 5)
        score = sliding.compute_merit("A")
        assert score < 30  # low because window shows all failures

    def test_window_gives_credit_for_recent_improvement(self):
        """Recent wins within window should be recognized."""
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        # 15 old losses, 5 recent wins
        _record_n(sliding, "A", [False] * 15 + [True] * 5)
        score = sliding.compute_merit("A")
        assert score > 70  # high because window shows all wins

    def test_base_floor_for_no_entries(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        assert sliding.compute_merit("new_guy") == 10.0

    def test_eliminated_minister_scores_zero(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 5)
        sliding.mark_eliminated("A")
        assert sliding.compute_merit("A") == 0.0


# ── compute_merit (exponential decay) ───────────────────────────────


class TestComputeMeritExpDecay:
    """EXP_DECAY: all entries weighted by recency."""

    def test_recent_entries_weighted_more(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, mode=WindowMode.EXP_DECAY, window_size=50)
        # Recent wins, old losses
        _record_n(sliding, "A", [False] * 20 + [True] * 5)
        score = sliding.compute_merit("A")
        # Should be above 30 because recent wins have higher weight
        assert score > 30

    def test_old_entries_fade(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, mode=WindowMode.EXP_DECAY, window_size=50)
        # Old wins, recent losses
        _record_n(sliding, "A", [True] * 20 + [False] * 5)
        score = sliding.compute_merit("A")
        # Recent losses drag down but old wins provide some cushion
        assert score < 60

    def test_all_same_outcome(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, mode=WindowMode.EXP_DECAY, window_size=50)
        _record_n(sliding, "A", [True] * 30)
        score = sliding.compute_merit("A")
        assert score > 70

    def test_decay_vs_hard_cutoff_different(self):
        """EXP_DECAY and HARD_CUTOFF should produce different results for same input."""
        outcomes = [False] * 30 + [True] * 10

        b1 = MeritBoard()
        s1 = SlidingMeritBoard(b1, window_size=20, mode=WindowMode.HARD_CUTOFF)
        _record_n(s1, "A", outcomes)

        b2 = MeritBoard()
        s2 = SlidingMeritBoard(b2, window_size=20, mode=WindowMode.EXP_DECAY)
        _record_n(s2, "A", outcomes)

        assert s1.compute_merit("A") != s2.compute_merit("A")


# ── compute_full_merit ──────────────────────────────────────────────


class TestComputeFullMerit:
    def test_full_merit_matches_underlying_board(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        _record_n(sliding, "A", [True] * 30)
        assert sliding.compute_full_merit("A") == board.compute_merit("A")

    def test_full_merit_differs_from_windowed_when_history_outside_window(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 20 + [False] * 5)
        windowed = sliding.compute_merit("A")
        full = sliding.compute_full_merit("A")
        assert windowed < full  # full includes old wins


# ── Ranking ─────────────────────────────────────────────────────────


class TestRanking:
    def test_ranking_sorted_by_windowed_merit(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        # A: recent wins, B: recent losses
        _record_n(sliding, "A", [True] * 5)
        _record_n(sliding, "B", [False] * 5)
        _record_n(sliding, "C", [True, True, False, False, True])

        ranking = sliding.get_ranking()
        assert ranking[0].minister == "A"
        assert ranking[-1].minister == "B"

    def test_ranking_returns_sliding_merit_reports(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        _record_n(sliding, "A", [True] * 5)
        ranking = sliding.get_ranking()
        assert isinstance(ranking[0], SlidingMeritReport)

    def test_top_n(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=3)
        _record_n(sliding, "A", [True] * 3)
        _record_n(sliding, "B", [True, False, True])
        _record_n(sliding, "C", [False] * 3)
        top2 = sliding.get_top_n(2)
        assert len(top2) == 2
        assert top2[0].minister == "A"

    def test_bottom_n(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=3)
        _record_n(sliding, "A", [True] * 3)
        _record_n(sliding, "B", [False] * 3)
        _record_n(sliding, "C", [True, False, False])
        bottom2 = sliding.get_bottom_n(2)
        assert len(bottom2) == 2
        assert bottom2[-1].minister in ("B", "C")

    def test_empty_ranking(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        assert sliding.get_ranking() == []


# ── Probation ───────────────────────────────────────────────────────


class TestProbation:
    def test_low_windowed_merit_triggers_probation(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 5)
        _record_n(sliding, "B", [False] * 5)
        candidates = sliding.get_probation_candidates()
        assert "B" in candidates
        assert "A" not in candidates

    def test_loss_streak_triggers_probation(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=10)
        # Even with moderate merit, 3+ consecutive losses flags
        _record_n(sliding, "A", [True] * 7 + [False, False, False])
        _record_n(sliding, "B", [True] * 10)
        _record_n(sliding, "C", [True] * 5)
        candidates = sliding.get_probation_candidates()
        assert "A" in candidates

    def test_eliminated_not_in_probation(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [False] * 5)
        sliding.mark_eliminated("A")
        candidates = sliding.get_probation_candidates()
        assert "A" not in candidates

    def test_no_spurious_probation_for_small_courts(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 5)
        candidates = sliding.get_probation_candidates()
        assert candidates == []  # single minister always top


# ── Volatility ──────────────────────────────────────────────────────


class TestVolatility:
    def test_stable_performer_low_volatility(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=20)
        _record_n(sliding, "A", [True] * 20)
        assert sliding.compute_volatility("A") < 0.05

    def test_volatile_performer_high_volatility(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=20)
        alternating = [True, False] * 10
        _record_n(sliding, "A", alternating)
        vol = sliding.compute_volatility("A")
        assert 0.45 < vol < 0.55

    def test_too_few_entries_returns_zero(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=20)
        _record_n(sliding, "A", [True] * 3)
        assert sliding.compute_volatility("A") == 0.0

    def test_empty_returns_zero(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        assert sliding.compute_volatility("nobody") == 0.0


# ── Momentum ────────────────────────────────────────────────────────


class TestMomentum:
    def test_improving_momentum_positive(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=10)
        _record_n(sliding, "A", [False] * 5 + [True] * 5)
        mom = sliding.compute_momentum("A")
        assert mom > 0.02

    def test_declining_momentum_negative(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=10)
        _record_n(sliding, "A", [True] * 5 + [False] * 5)
        mom = sliding.compute_momentum("A")
        assert mom < -0.02

    def test_stable_momentum_near_zero(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=20)
        _record_n(sliding, "A", [True, False] * 10)
        mom = sliding.compute_momentum("A")
        assert abs(mom) < 0.05

    def test_too_few_entries_returns_zero(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=20)
        _record_n(sliding, "A", [True] * 3)
        assert sliding.compute_momentum("A") == 0.0

    def test_empty_returns_zero(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        assert sliding.compute_momentum("nobody") == 0.0


# ── SlidingMeritReport ──────────────────────────────────────────────


class TestSlidingMeritReport:
    def test_report_includes_windowed_fields(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 10)  # more than window
        report = sliding._build_report("A")

        assert report.window_size == 5
        assert report.entries_in_window == 5
        assert report.windowed_merit > 0
        assert report.full_merit > 0
        assert isinstance(report.merit_delta, float)
        assert 0 <= report.volatility <= 0.5
        assert -0.2 <= report.momentum <= 0.2

    def test_delta_shows_windowed_vs_full(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=3)
        # Old wins → high full merit. Recent losses → low windowed merit.
        _record_n(sliding, "A", [True] * 20 + [False] * 3)
        report = sliding._build_report("A")
        assert report.merit_delta < 0  # windowed worse than full

    def test_report_for_empty_minister(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        report = sliding._build_report("new")
        assert report.minister == "new"
        assert report.total_dispatches == 0
        assert report.windowed_merit == 10.0
        assert report.full_merit == 10.0


# ── Leaderboard ─────────────────────────────────────────────────────


class TestLeaderboard:
    def test_leaderboard_structure(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 5)
        _record_n(sliding, "B", [False] * 5)

        lb = sliding.get_leaderboard()
        assert lb["window_size"] == 5
        assert lb["mode"] == "HARD_CUTOFF"
        assert len(lb["rankings"]) == 2

    def test_leaderboard_fields(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 5)

        lb = sliding.get_leaderboard()
        r = lb["rankings"][0]
        assert "windowed_merit" in r
        assert "full_merit" in r
        assert "merit_delta" in r
        assert "momentum" in r
        assert "entries_in_window" in r


# ── Passthrough operations ──────────────────────────────────────────


class TestPassthrough:
    def test_record_dispatch_adds_to_underlying_board(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        sliding.record_dispatch("A", "e1", "test", True, 0.9)
        assert len(board._ledger["A"]) == 1

    def test_record_feedback_delegates(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        sliding.record_dispatch("A", "e1", "test", True, 0.5)
        assert sliding.record_feedback("A", "e1", 0.9)

    def test_mark_eliminated_syncs(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board)
        _record_n(sliding, "A", [True] * 5)
        sliding.mark_eliminated("A")
        assert board.is_eliminated("A")
        assert sliding.is_eliminated("A")


# ── Edge cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    def test_single_entry_window(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=1)
        _record_n(sliding, "A", [True, False, True])
        score = sliding.compute_merit("A")
        # Window only has last entry (True)
        assert score > 50

    def test_window_exactly_history_length(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=10)
        _record_n(sliding, "A", [True] * 10)
        # windowed score excludes recency bonus, so differs from full
        w_score = sliding.compute_merit("A")
        f_score = sliding.compute_full_merit("A")
        assert w_score > 60
        assert f_score > 70

    def test_multiple_ministers_isolated_windows(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=3)
        _record_n(sliding, "A", [True] * 10)
        _record_n(sliding, "B", [False] * 10)
        assert sliding.compute_merit("A") > 70
        assert sliding.compute_merit("B") < 30

    def test_window_size_zero_clamped(self):
        board = MeritBoard()
        s = SlidingMeritBoard(board, window_size=0)
        assert s.window_size == 3  # clamped to min

    def test_sliding_after_elimination(self):
        board = MeritBoard()
        sliding = SlidingMeritBoard(board, window_size=5)
        _record_n(sliding, "A", [True] * 5)
        _record_n(sliding, "B", [False] * 5)
        sliding.mark_eliminated("A")

        ranking = sliding.get_ranking()
        # Eliminated still appears but with 0 merit
        a_report = next(r for r in ranking if r.minister == "A")
        assert a_report.windowed_merit == 0.0
        assert a_report.eliminated
