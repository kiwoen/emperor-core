"""
MeritBoard tests — 功勋榜单元测试 + 集成测试.

Covers:
    - Dispatch recording and retrieval
    - Merit score computation (all weights, recency decay)
    - Ranking and position assignment
    - MeritRank tier assignment
    - Bottom-N and probation candidate detection
    - Streak tracking and trend analysis
    - Feedback recording
    - Elimination marking
    - Edge cases: empty board, new minister, extreme scores
"""

import pytest
from jarvis.court.merit_board import (
    DispatchEntry,
    MeritBoard,
    MeritRank,
    MeritReport,
)


class TestDispatchRecording:
    """Dispatch recording and basic retrieval."""

    def test_record_single_dispatch(self):
        board = MeritBoard()
        board.record_dispatch(
            "丞相", edict_id="e1", intent="分析代码",
            success=True, confidence=0.9,
        )
        assert "丞相" in board._ledger
        assert len(board._ledger["丞相"]) == 1
        assert board._ledger["丞相"][0].success is True
        assert board._ledger["丞相"][0].confidence == 0.9

    def test_record_multiple_ministers(self):
        board = MeritBoard()
        board.record_dispatch("丞相", "e1", "分析代码", True, 0.9)
        board.record_dispatch("工部尚书", "e2", "写代码", True, 0.85)
        board.record_dispatch("太史令", "e3", "搜索", False, 0.2)

        assert len(board._ledger) == 3
        assert len(board._ledger["丞相"]) == 1
        assert len(board._ledger["工部尚书"]) == 1
        assert len(board._ledger["太史令"]) == 1

    def test_record_multiple_dispatches_same_minister(self):
        board = MeritBoard()
        for i in range(10):
            board.record_dispatch(
                "丞相", f"e{i}", f"task {i}",
                success=i % 2 == 0, confidence=0.5 + i * 0.03,
            )
        assert len(board._ledger["丞相"]) == 10


class TestMeritScoring:
    """Composite merit score computation."""

    def test_empty_minister_gets_baseline(self):
        board = MeritBoard()
        board._ledger["新大臣"] = []
        score = board.compute_merit("新大臣")
        assert score == 10.0  # floor

    def test_perfect_minister_gets_max(self):
        board = MeritBoard()
        for i in range(50):
            board.record_dispatch(
                "完美大臣", f"e{i}", f"task {i}",
                success=True, confidence=1.0,
            )
            board.record_feedback("完美大臣", f"e{i}", 1.0)
        score = board.compute_merit("完美大臣")
        assert score > 90  # near perfect

    def test_complete_failure_gets_low(self):
        board = MeritBoard()
        for i in range(20):
            board.record_dispatch(
                "废物大臣", f"e{i}", f"task {i}",
                success=False, confidence=0.1,
            )
        score = board.compute_merit("废物大臣")
        assert score < 30
        assert score >= 10.0  # floor respected

    def test_mixed_performance(self):
        board = MeritBoard()
        # 7 successes, 3 failures
        for i in range(10):
            success = i < 7
            board.record_dispatch(
                "正常大臣", f"e{i}", f"task {i}",
                success=success, confidence=0.7 if success else 0.3,
            )
        score = board.compute_merit("正常大臣")
        assert 40 < score < 80

    def test_recency_matters(self):
        """Recent successes should score higher than old successes."""
        board = MeritBoard()
        # 10 failures then 10 successes
        for i in range(10):
            board.record_dispatch("逆转大臣", f"e{i}", f"task {i}", False, 0.2)
        for i in range(10, 20):
            board.record_dispatch("逆转大臣", f"e{i}", f"task {i}", True, 0.9)

        # Same 50% success rate, but recency should push it up
        score = board.compute_merit("逆转大臣")
        # 50% success but recent wins → recency bonus kicks in
        assert score >= 40  # should be decent due to recency

    def test_feedback_boosts_score(self):
        board = MeritBoard()
        board.record_dispatch("丞相", "e1", "task", True, 0.8)
        board.record_feedback("丞相", "e1", 1.0)

        # With feedback 1.0 vs default 0.5
        score_with_feedback = board.compute_merit("丞相")

        # Compare without feedback
        board2 = MeritBoard()
        board2.record_dispatch("丞相", "e1", "task", True, 0.8)
        score_without = board2.compute_merit("丞相")

        assert score_with_feedback > score_without


class TestRanking:
    """Ranking and position assignment."""

    def test_ranking_sorts_by_merit_descending(self):
        board = MeritBoard()
        # 丞相: high performer
        for _ in range(10):
            board.record_dispatch("丞相", f"e{_}", "task", True, 0.9)
        # 太卜: medium
        for _ in range(10):
            board.record_dispatch("太卜", f"e{_}", "task",
                                _ < 6, 0.5)
        # 卫尉: poor
        for _ in range(10):
            board.record_dispatch("卫尉", f"e{_}", "task",
                                _ < 3, 0.3)

        ranking = board.get_ranking()
        assert ranking[0].minister == "丞相"
        assert ranking[-1].minister == "卫尉"

    def test_ranking_positions_sequential(self):
        board = MeritBoard()
        for name in ["A", "B", "C"]:
            board.record_dispatch(name, f"e_{name}", "task", True, 0.8)

        ranking = board.get_ranking()
        positions = {r.court_position for r in ranking}
        assert positions == {1, 2, 3}

    def test_top_n(self):
        board = MeritBoard()
        for i, name in enumerate(["A", "B", "C", "D"]):
            for _ in range(5):
                success = name in ["A", "B"]
                board.record_dispatch(name, f"e{_}", "task", success, 0.8)

        top2 = board.get_top_n(2)
        assert len(top2) == 2
        assert {r.minister for r in top2} == {"A", "B"}

    def test_bottom_n(self):
        board = MeritBoard()
        for i, name in enumerate(["A", "B", "C", "D"]):
            for _ in range(5):
                success = name in ["A", "B"]
                board.record_dispatch(name, f"e{_}", "task", success, 0.8)

        bottom2 = board.get_bottom_n(2)
        assert len(bottom2) == 2
        assert {r.minister for r in bottom2} == {"C", "D"}


class TestMeritRankTiers:
    """Tier assignment from merit score."""

    def test_commoner(self):
        assert MeritRank.from_score(0) == MeritRank.COMMONER
        assert MeritRank.from_score(10) == MeritRank.COMMONER
        assert MeritRank.from_score(19.9) == MeritRank.COMMONER

    def test_knight(self):
        assert MeritRank.from_score(20) == MeritRank.KNIGHT
        assert MeritRank.from_score(35) == MeritRank.KNIGHT

    def test_officer(self):
        assert MeritRank.from_score(40) == MeritRank.OFFICER
        assert MeritRank.from_score(55) == MeritRank.OFFICER

    def test_minister(self):
        assert MeritRank.from_score(60) == MeritRank.MINISTER
        assert MeritRank.from_score(75) == MeritRank.MINISTER

    def test_grandee(self):
        assert MeritRank.from_score(80) == MeritRank.GRANDEE
        assert MeritRank.from_score(95) == MeritRank.GRANDEE


class TestProbationDetection:
    """Probation candidate identification."""

    def test_bottom_performer_enters_probation(self):
        board = MeritBoard()
        # 8 ministers, bottom 25% → 2 enter probation
        for i, name in enumerate(
            ["强A", "强B", "强C", "中D", "中E", "弱F", "弱G", "弱H"]
        ):
            for j in range(10):
                success = i < 5 or (i < 6 and j < 5)
                board.record_dispatch(name, f"e{j}", "task", success, 0.6 if success else 0.2)

        candidates = board.get_probation_candidates()
        # Bottom 25% with score < 30 should appear
        assert len(candidates) > 0

    def test_streak_triggers_probation(self):
        board = MeritBoard()
        # Normal but then 3 consecutive failures
        board.record_dispatch("丞相", "e1", "task", True, 0.8)
        board.record_dispatch("丞相", "e2", "task", True, 0.8)
        board.record_dispatch("丞相", "e3", "task", False, 0.2)
        board.record_dispatch("丞相", "e4", "task", False, 0.1)
        board.record_dispatch("丞相", "e5", "task", False, 0.1)

        # After 3 consecutive failures, should appear in probation
        # But merit might still be high enough → depends on scoring
        # Just verify the detection logic runs
        candidates = board.get_probation_candidates()
        # Even if not in bottom %, the 3-streak should trigger
        assert "丞相" in candidates


class TestStreakAndTrend:
    """Win/loss streaks and performance trends."""

    def test_win_streak_positive(self):
        board = MeritBoard()
        for i in range(5):
            board.record_dispatch("连胜大臣", f"e{i}", "task", True, 0.9)
        report = board._build_report("连胜大臣")
        assert report.streak == 5

    def test_loss_streak_negative(self):
        board = MeritBoard()
        for i in range(4):
            board.record_dispatch("连败大臣", f"e{i}", "task", False, 0.1)
        report = board._build_report("连败大臣")
        assert report.streak == -4

    def test_mixed_streak_tracks_most_recent(self):
        board = MeritBoard()
        board.record_dispatch("混合大臣", "e1", "task", True, 0.9)
        board.record_dispatch("混合大臣", "e2", "task", True, 0.9)
        board.record_dispatch("混合大臣", "e3", "task", False, 0.1)
        board.record_dispatch("混合大臣", "e4", "task", False, 0.1)
        report = board._build_report("混合大臣")
        assert report.streak == -2  # most recent

    def test_rising_trend(self):
        board = MeritBoard()
        # First half: mostly failures
        for i in range(5):
            board.record_dispatch("上升大臣", f"e{i}", "task", False, 0.2)
        # Second half: mostly successes
        for i in range(5, 10):
            board.record_dispatch("上升大臣", f"e{i}", "task", True, 0.9)
        report = board._build_report("上升大臣")
        assert report.recent_trend == "rising"

    def test_falling_trend(self):
        board = MeritBoard()
        for i in range(5):
            board.record_dispatch("下降大臣", f"e{i}", "task", True, 0.9)
        for i in range(5, 10):
            board.record_dispatch("下降大臣", f"e{i}", "task", False, 0.2)
        report = board._build_report("下降大臣")
        assert report.recent_trend == "falling"

    def test_stable_trend(self):
        board = MeritBoard()
        # First half: 3/5 success; second half: 3/5 success → stable
        results = [True, False, True, True, False,   # first half 3/5
                   True, False, True, False, True]   # second half 3/5
        for i, success in enumerate(results):
            board.record_dispatch("稳定大臣", f"e{i}", "task", success, 0.6)
        report = board._build_report("稳定大臣")
        assert report.recent_trend == "stable"


class TestFeedback:
    """Feedback recording and matching."""

    def test_feedback_exact_match(self):
        board = MeritBoard()
        board.record_dispatch("丞相", "e1", "task", True, 0.8)
        result = board.record_feedback("丞相", "e1", 0.9)
        assert result is True
        assert board._ledger["丞相"][0].feedback_score == 0.9

    def test_feedback_prefix_match(self):
        """Matches decree_id::minister pattern."""
        board = MeritBoard()
        board.record_dispatch("丞相", "decree_1::丞相", "task", True, 0.8)
        result = board.record_feedback("丞相", "decree_1", 0.7)
        assert result is True
        assert board._ledger["丞相"][0].feedback_score == 0.7

    def test_feedback_clamped(self):
        board = MeritBoard()
        board.record_dispatch("丞相", "e1", "task", True, 0.8)
        board.record_feedback("丞相", "e1", 1.5)
        assert board._ledger["丞相"][0].feedback_score == 1.0

        board.record_feedback("丞相", "e1", -0.5)
        assert board._ledger["丞相"][0].feedback_score == 0.0

    def test_feedback_not_found(self):
        board = MeritBoard()
        result = board.record_feedback("不存在", "e999", 0.5)
        assert result is False


class TestElimination:
    """Elimination marking."""

    def test_elimination_sets_flag(self):
        board = MeritBoard()
        board.record_dispatch("淘汰大臣", "e1", "task", False, 0.1)
        board.mark_eliminated("淘汰大臣")
        assert board.is_eliminated("淘汰大臣")
        assert board.compute_merit("淘汰大臣") == 0.0

    def test_eliminated_not_in_ranking(self):
        board = MeritBoard()
        board.record_dispatch("A", "e1", "task", True, 0.9)
        board.record_dispatch("B", "e1", "task", True, 0.8)
        board.mark_eliminated("B")

        ranking = board.get_ranking()
        minister_names = {r.minister for r in ranking}
        assert "A" in minister_names
        # Eliminated ministers get 0 merit, still appear in ranking
        # but with eliminated=True
        eliminated = [r for r in ranking if r.eliminated]
        assert len(eliminated) == 1
        assert eliminated[0].minister == "B"

    def test_eliminated_excluded_from_probation(self):
        board = MeritBoard()
        board.record_dispatch("弱A", "e1", "task", False, 0.1)
        board.record_dispatch("弱B", "e1", "task", False, 0.1)
        board.mark_eliminated("弱B")

        candidates = board.get_probation_candidates()
        assert "弱B" not in candidates


class TestLeaderboard:
    """Leaderboard output format."""

    def test_leaderboard_structure(self):
        board = MeritBoard()
        board.record_dispatch("丞相", "e1", "task", True, 0.9)
        board.record_dispatch("太卜", "e2", "task", True, 0.85)

        lb = board.get_leaderboard()
        assert "total_ministers" in lb
        assert "eliminated" in lb
        assert "rankings" in lb
        assert lb["total_ministers"] == 2
        assert len(lb["rankings"]) == 2

    def test_leaderboard_rankings_have_required_fields(self):
        board = MeritBoard()
        board.record_dispatch("丞相", "e1", "task", True, 0.9)

        lb = board.get_leaderboard()
        r = lb["rankings"][0]
        assert "position" in r
        assert "minister" in r
        assert "rank" in r
        assert "merit_score" in r
        assert "success_rate" in r
        assert "avg_confidence" in r
        assert "streak" in r
        assert "trend" in r


class TestRecencyDecay:
    """Exponential recency decay math."""

    def test_recent_only_scores_higher(self):
        """Same total performance, but recent-only should score higher
        than ancient-only."""
        board_recent = MeritBoard()
        for i in range(20):
            board_recent.record_dispatch("R", f"e{i}", "t", i >= 5, 0.8)

        board_ancient = MeritBoard()
        for i in range(20):
            board_ancient.record_dispatch("A", f"e{i}", "t", i < 15, 0.8)

        # Both: 15/20 = 75% success, but R has recent wins, A has ancient wins
        score_r = board_recent.compute_merit("R")
        score_a = board_ancient.compute_merit("A")
        assert score_r > score_a
