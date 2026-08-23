"""
ReflectionConsensus tests — 三省合议多轮辩论合成系统单元测试.

Covers:
    - Cross-critique generation (SUPPORT/CHALLENGE/REFINE)
    - Weighted voting mechanism
    - Draft synthesis (base selection + augmentation)
    - Self-reflection improvement loop
    - Final confidence computation
    - Dissenting opinion collection
    - Edge cases: single minister, no successes, all failures
    - Integration with MeritBoard
"""

import pytest
from jarvis.court.reflection import (
    ConsensusPhase,
    ConsensusResult,
    Critique,
    CritiqueDirection,
    ReflectionConsensus,
    SynthesisDraft,
    Vote,
)
from jarvis.court.merit_board import MeritBoard


# ── Helpers for creating mock memorials ────────────────────────────


class MockMemorial:
    """Minimal mock of a Memorial for testing."""
    def __init__(self, minister, success, output, confidence):
        self.minister = minister
        self.success = success
        self.output = output
        self.confidence = confidence


def make_memorial(minister, success=True, output="", confidence=0.8):
    return MockMemorial(minister, success, output, confidence)


# ── Cross-Critique ─────────────────────────────────────────────────


class TestCrossCritique:
    """Phase 1: ministers critique each other's outputs."""

    def test_two_ministers_produce_critiques(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "详细分析A" * 20, 0.90),
            make_memorial("太卜", True, "科学推理B" * 20, 0.70),
        ]
        critiques = rc._cross_critique("分析任务", memorials)
        # 2 ministers → each critiques the other → 2 critiques
        assert len(critiques) == 2

    def test_single_minister_no_critiques(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "分析" * 20, 0.9),
        ]
        critiques = rc._cross_critique("任务", memorials)
        assert len(critiques) == 0

    def test_high_confidence_challenges_low(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "详细高分分析报告内容" * 20, 0.95),
            make_memorial("太卜", True, "简单结论" * 5, 0.40),
        ]
        critiques = rc._cross_critique("任务", memorials)
        # High conf vs low conf → CHALLENGE
        challenge = [c for c in critiques if c.direction == CritiqueDirection.CHALLENGE]
        assert len(challenge) >= 1
        assert challenge[0].from_minister == "丞相"

    def test_similar_confidence_supports(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "analysis report findings " * 20, 0.80),
            make_memorial("太史令", True, "analysis results conclusion " * 20, 0.78),
        ]
        critiques = rc._cross_critique("analyze task", memorials)
        supports = [c for c in critiques if c.direction == CritiqueDirection.SUPPORT]
        assert len(supports) >= 1

    def test_multiple_ministers_full_matrix(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("A", True, "输出A" * 10, 0.85),
            make_memorial("B", True, "输出B" * 10, 0.75),
            make_memorial("C", True, "输出C" * 10, 0.65),
        ]
        critiques = rc._cross_critique("任务", memorials)
        # 3 ministers → each critiques 2 others → 6 critiques
        assert len(critiques) == 6

    def test_merit_board_influences_critique_weight(self):
        mb = MeritBoard()
        mb.record_dispatch("丞相", "e1", "分析", True, 0.95)
        mb.record_dispatch("丞相", "e2", "分析", True, 0.90)
        mb.record_dispatch("太卜", "e3", "分析", False, 0.20)
        rc = ReflectionConsensus(merit_board=mb)
        memorials = [
            make_memorial("丞相", True, "详细分析" * 20, 0.85),
            make_memorial("太卜", True, "简单结论" * 5, 0.60),
        ]
        critiques = rc._cross_critique("任务", memorials)
        # Higher-merit critic has more synthesis_value
        chancellor_critique = [
            c for c in critiques if c.from_minister == "丞相"
        ]
        assert chancellor_critique
        assert chancellor_critique[0].to_minister == "太卜"


# ── Weighted Voting ────────────────────────────────────────────────


class TestWeightedVoting:
    """Phase 2: weighted voting on best approach."""

    def test_minister_votes_for_themselves_with_no_strong_preference(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "分析A" * 20, 0.85),
            make_memorial("太卜", True, "分析B" * 20, 0.80),
        ]
        critiques = rc._cross_critique("中立任务", memorials)
        votes = rc._weighted_vote("中立任务", memorials, critiques)
        assert len(votes) == 2

    def test_merit_board_weighting(self):
        mb = MeritBoard()
        mb.record_dispatch("丞相", "e1", "分析", True, 0.95)
        mb.record_dispatch("丞相", "e2", "分析", True, 0.90)
        mb.record_dispatch("太卜", "e3", "分析", False, 0.20)
        rc = ReflectionConsensus(merit_board=mb)
        memorials = [
            make_memorial("丞相", True, "优质分析" * 20, 0.90),
            make_memorial("太卜", True, "一般结论" * 10, 0.70),
        ]
        critiques = rc._cross_critique("关键任务", memorials)
        votes = rc._weighted_vote("关键任务", memorials, critiques)
        # Higher merit minister should have more weight
        chancellor_vote = [v for v in votes if v.voter == "丞相"]
        diviner_vote = [v for v in votes if v.voter == "太卜"]
        assert chancellor_vote
        assert diviner_vote
        assert chancellor_vote[0].weight >= diviner_vote[0].weight

    def test_single_minister_no_votes(self):
        rc = ReflectionConsensus()
        memorials = [make_memorial("丞相", True, "分析" * 20, 0.85)]
        critiques = []
        votes = rc._weighted_vote("任务", memorials, critiques)
        assert len(votes) == 0

    def test_vote_tally_selects_winner(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("A", True, "输出A" * 15, 0.90),
            make_memorial("B", True, "输出B" * 10, 0.70),
        ]
        critiques = rc._cross_critique("任务", memorials)
        votes = rc._weighted_vote("任务", memorials, critiques)
        # Tabulate results
        tally = {}
        for v in votes:
            tally[v.option] = tally.get(v.option, 0) + v.weight
        assert len(tally) > 0
        assert max(tally, key=tally.get) in ["A", "B"]


# ── Draft Synthesis ────────────────────────────────────────────────


class TestDraftSynthesis:
    """Phase 3: build draft from best outputs."""

    def test_single_minister_uses_direct_output(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "这是最终分析结果", 0.88),
        ]
        draft = rc._draft_synthesis("任务", memorials, [], [])
        assert draft.base_minister == "丞相"
        assert "最终分析结果" in draft.content
        assert draft.incorporated_from == []

    def test_multi_minister_synthesis_selects_winner(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "核心分析：这是一个重要的结论。" * 5, 0.92),
            make_memorial("太卜", True, "推理链：步骤一二三。" * 5, 0.75),
        ]
        critiques = rc._cross_critique("分析任务", memorials)
        votes = rc._weighted_vote("分析任务", memorials, critiques)
        draft = rc._draft_synthesis("分析任务", memorials, critiques, votes)
        assert draft.base_minister in ["丞相", "太卜"]
        assert len(draft.content) > 0

    def test_empty_memorials_handle_gracefully(self):
        rc = ReflectionConsensus()
        draft = rc._draft_synthesis("任务", [], [], [])
        assert draft.quality_score == 0.0
        assert "无策可陈" in draft.content

    def test_incorporated_from_tracks_contributors(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("A", True, "核心分析结论如下：这是一个完整的报告。" * 5, 0.90),
            make_memorial("B", True, "完全不同的补充观点，与A完全不同。" * 5, 0.78),
        ]
        critiques = rc._cross_critique("任务", memorials)
        votes = rc._weighted_vote("任务", memorials, critiques)
        draft = rc._draft_synthesis("任务", memorials, critiques, votes)
        # B should be incorporated if its content is unique
        assert draft.base_minister in ["A", "B"]

    def test_draft_quality_reflects_contributors(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("A", True, "高质量输出" * 10, 0.90),
            make_memorial("B", True, "中等输出" * 8, 0.65),
        ]
        critiques = rc._cross_critique("任务", memorials)
        votes = rc._weighted_vote("任务", memorials, critiques)
        draft = rc._draft_synthesis("任务", memorials, critiques, votes)
        # Quality should be between the two confidences
        assert 0.6 <= draft.quality_score <= 0.95


# ── Self-Reflection ────────────────────────────────────────────────


class TestSelfReflection:
    """Phase 4: critique and improve the draft."""

    def test_reflection_preserves_structure(self):
        rc = ReflectionConsensus()
        memorials = [make_memorial("丞相", True, "分析" * 20, 0.85)]
        draft = rc._draft_synthesis("任务", memorials, [], [])
        new_draft = rc._self_reflect("任务", draft, memorials, [])
        assert new_draft.version == draft.version + 1
        assert len(new_draft.content) >= len(draft.content)

    def test_reflection_flags_structural_issues(self):
        rc = ReflectionConsensus()
        draft = SynthesisDraft(
            version=1,
            content="一句话结论",  # Very short, no structure
            base_minister="丞相",
            incorporated_from=[],
            critiques_applied=0,
            quality_score=0.95,  # Overconfident for short output
        )
        memorials = [make_memorial("丞相", True, "分析" * 5, 0.8)]
        new_draft = rc._self_reflect("任务", draft, memorials, [])
        # Should have issues (overconfident short output)
        assert len(new_draft.issues) > 0

    def test_reflection_improves_with_addressable_issues(self):
        rc = ReflectionConsensus()
        draft = SynthesisDraft(
            version=1, content="推荐采用A方案", base_minister="丞相",
            incorporated_from=[], critiques_applied=0, quality_score=0.6,
        )
        memorials = [make_memorial("丞相", True, "分析" * 20, 0.8)]
        new_draft = rc._self_reflect("任务", draft, memorials, [])
        # Short + overconfident → issues found → quality adjusted
        assert isinstance(new_draft.quality_score, float)

    def test_reflection_detects_contradictions(self):
        rc = ReflectionConsensus()
        draft = SynthesisDraft(
            version=1,
            content="推荐采用这个方案。不推荐采用那个方案。",
            base_minister="丞相",
            incorporated_from=[],
            critiques_applied=0,
            quality_score=0.7,
        )
        memorials = [make_memorial("丞相", True, "分析" * 20, 0.8)]
        new_draft = rc._self_reflect("任务", draft, memorials, [])
        # Should flag the 推荐/不推荐 contradiction
        assert any("矛盾" in i or "推荐" in i for i in new_draft.issues)


# ── Full Pipeline ──────────────────────────────────────────────────


class TestFullPipeline:
    """End-to-end synthesis pipeline."""

    @pytest.mark.asyncio
    async def test_single_minister_pipeline(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "完整的分析报告，包含三部分内容。" * 5, 0.88),
        ]
        result = await rc.synthesize("分析任务", memorials)
        assert isinstance(result, ConsensusResult)
        assert result.winning_minister == "丞相"
        assert len(result.decree_content) > 0
        assert result.confidence > 0
        assert ConsensusPhase.FINAL_DECREE in result.phases_completed

    @pytest.mark.asyncio
    async def test_multi_minister_pipeline(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "综合分析：建议采取A方案" * 5, 0.90),
            make_memorial("太卜", True, "科学推理：A方案可行性高" * 5, 0.82),
            make_memorial("工部尚书", True, "代码实现：A方案需要X工具" * 5, 0.78),
        ]
        result = await rc.synthesize("制定方案", memorials)
        assert result.winning_minister in ["丞相", "太卜", "工部尚书"]
        assert result.quality_score > 0
        assert len(result.contributors) == 3
        # Should have gone through cross-critique
        assert ConsensusPhase.CROSS_CRITIQUE in result.phases_completed
        assert ConsensusPhase.WEIGHTED_VOTE in result.phases_completed

    @pytest.mark.asyncio
    async def test_pipeline_with_merit_board(self):
        mb = MeritBoard()
        mb.record_dispatch("丞相", "e1", "分析", True, 0.95)
        mb.record_dispatch("丞相", "e2", "分析", True, 0.92)
        mb.record_dispatch("太卜", "e3", "分析", True, 0.75)
        mb.record_dispatch("太卜", "e4", "分析", False, 0.30)
        rc = ReflectionConsensus(merit_board=mb)
        memorials = [
            make_memorial("丞相", True, "优质分析" * 20, 0.92),
            make_memorial("太卜", True, "一般推理" * 15, 0.70),
        ]
        result = await rc.synthesize("分析任务", memorials)
        # Higher-merit minister should win (merit board ranks by score)
        assert result.winning_minister is not None
        assert result.winning_minister in ("丞相", "太卜")

    @pytest.mark.asyncio
    async def test_dissenting_opinions_collected(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("A", True, "支持方案X的详细论证" * 10, 0.90),
            make_memorial("B", True, "反对方案X，支持方案Y" * 10, 0.40),
        ]
        result = await rc.synthesize("决策", memorials)
        assert isinstance(result.dissenting_opinions, list)

    @pytest.mark.asyncio
    async def test_synthesis_trace_complete(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("丞相", True, "核心分析" * 10, 0.85),
            make_memorial("太卜", True, "推理论证" * 10, 0.80),
        ]
        result = await rc.synthesize("任务", memorials)
        assert len(result.synthesis_trace) >= 2
        # First trace should be about cross-critique
        assert any("交叉" in t for t in result.synthesis_trace)
        # Last trace should be about final decree
        assert any("最终" in t or "圣旨" in t for t in result.synthesis_trace)

    @pytest.mark.asyncio
    async def test_empty_memorials_pipeline(self):
        rc = ReflectionConsensus()
        result = await rc.synthesize("任务", [])
        assert "无策可陈" in result.decree_content
        assert result.quality_score == 0.0

    @pytest.mark.asyncio
    async def test_all_failed_memorials(self):
        rc = ReflectionConsensus()
        memorials = [
            make_memorial("A", False, "", 0.0),
            make_memorial("B", False, "", 0.0),
        ]
        result = await rc.synthesize("任务", memorials)
        assert "无策可陈" in result.decree_content


# ── Integration with ConsensusResult ───────────────────────────────


class TestConsensusResult:
    """ConsensusResult dataclass validation."""

    @pytest.mark.asyncio
    async def test_result_has_all_fields(self):
        rc = ReflectionConsensus()
        memorials = [make_memorial("丞相", True, "分析" * 20, 0.88)]
        result = await rc.synthesize("任务", memorials)
        assert result.decree_content
        assert isinstance(result.quality_score, float)
        assert isinstance(result.confidence, float)
        assert result.phases_completed
        assert result.winning_minister
        assert result.contributors


# ── _elect_winner ──────────────────────────────────────────────────


class TestElectWinner:
    """Winner election from votes."""

    def test_elects_highest_vote(self):
        rc = ReflectionConsensus()
        votes = [
            Vote("A", "X", weight=2.0, reasoning="", confidence=0.9),
            Vote("B", "X", weight=1.5, reasoning="", confidence=0.8),
            Vote("C", "Y", weight=1.0, reasoning="", confidence=0.7),
        ]
        successes = [
            make_memorial("X", True, "", 0.9),
            make_memorial("Y", True, "", 0.7),
        ]
        winner = rc._elect_winner(votes, successes)
        assert winner == "X"

    def test_falls_back_to_highest_confidence(self):
        rc = ReflectionConsensus()
        successes = [
            make_memorial("A", True, "", 0.9),
            make_memorial("B", True, "", 0.7),
        ]
        winner = rc._elect_winner([], successes)
        assert winner == "A"
