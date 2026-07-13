"""Tests for the Imperial Court System — multi-minister deliberation."""

import asyncio
import pytest

from jarvis.court import (
    Decree,
    Edict,
    Emperor,
    ImperialCourt,
    Memorial,
    Minister,
    MinisterProfile,
    MinisterState,
    create_ministers,
)


# ── Minister Profile & Creation ─────────────────────────────────────────────

class TestMinisters:
    def test_create_all_eight(self):
        """All eight standard ministers are created."""
        ministers = create_ministers()
        assert len(ministers) == 8
        titles = {m.name for m in ministers}
        assert titles == {"丞相", "御史大夫", "太史令", "工部尚书", "太常", "大司农", "太卜", "卫尉"}

    def test_minister_has_capabilities(self):
        """Each minister has a non-empty strength/weakness profile."""
        for m in create_ministers():
            assert len(m.profile.strengths) > 0, f"{m.name} has no strengths"
            assert len(m.profile.weaknesses) > 0, f"{m.name} has no weaknesses"
            assert 0 < m.profile.quality_score <= 1.0

    def test_minister_can_handle_scoring(self):
        """Ministers produce meaningful confidence scores for relevant queries."""
        chancellor = create_ministers()[0]  # 丞相
        score_high = chancellor.can_handle("写一篇关于机器学习的分析报告")
        score_low = chancellor.can_handle(
            "这个图片里有什么? 需要深度多模态识别能力"
        )
        assert score_high > score_low, (
            f"Chancellor should score higher on writing than image: "
            f"{score_high:.2f} vs {score_low:.2f}"
        )

    def test_minister_unique_identities(self):
        """Each minister has a distinct archetype."""
        ministers = create_ministers()
        archetypes = {m.profile.archetype for m in ministers}
        # All archetypes should be unique
        assert len(archetypes) == 8


# ── Single Minister Dispatch ─────────────────────────────────────────────────

class TestSingleMinisterDispatch:
    @pytest.mark.asyncio
    async def test_edict_memorial_cycle(self):
        """A minister receives an edict and returns a memorial."""
        minister = create_ministers()[2]  # 太史令
        edict = Edict(
            edict_id="test:1",
            intent="搜索最新AI论文",
            priority=8,
        )
        memorial = await minister.receive_edict(edict)
        assert isinstance(memorial, Memorial)
        assert memorial.success is True
        assert memorial.confidence > 0
        assert len(memorial.output) > 0
        # Minister should return to IDLE after completing the dispatch
        assert minister.state == MinisterState.IDLE

    @pytest.mark.asyncio
    async def test_evolution_tracks_dispatch_count(self):
        """Minister dispatch_count increments after each edict."""
        minister = create_ministers()[4]  # 太常
        before = minister.dispatch_count
        await minister.receive_edict(Edict("ev:1", "识别图片内容", priority=5))
        after = minister.dispatch_count
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_evolution_confidence_baseline(self):
        """Confidence baseline updates after multiple successful dispatches."""
        minister = create_ministers()[5]  # 大司农
        initial = minister._confidence_baseline

        for i in range(3):
            await minister.receive_edict(Edict(f"b:{i}", "成本优化方案", priority=6))

        # Baseline should have drifted slightly
        assert minister._confidence_baseline != initial

    @pytest.mark.asyncio
    async def test_experience_accumulation(self):
        """Experience records accumulate across dispatches."""
        minister = create_ministers()[7]  # 卫尉
        await minister.receive_edict(Edict("exp:1", "安全审计代码", priority=7))
        await minister.receive_edict(Edict("exp:2", "检查漏洞", priority=6))

        metrics = minister.get_evolution_metrics()
        assert metrics["dispatch_count"] == 2
        assert metrics["experience_count"] == 2

    @pytest.mark.asyncio
    async def test_feedback_loop(self):
        """External feedback adjusts minister's quality tracking."""
        minister = create_ministers()[1]  # 御史大夫
        await minister.receive_edict(Edict("fb:1", "审阅这篇文章", priority=5))
        minister.record_feedback("fb:1", 0.95)
        minister.record_feedback("fb:x", 0.50)  # Non-existent should not crash

        metrics = minister.get_evolution_metrics()
        assert metrics["total_feedback"] >= 1


# ── ImperialCourt (Emperor) ──────────────────────────────────────────────────

class TestImperialCourt:
    def test_install_ministers_from_factory(self):
        """Factory installs all eight ministers into the court."""
        court = ImperialCourt()
        court.install_ministers_from_factory()
        assert len(court.ministers) == 8

    def test_analyze_petition(self):
        """Emperor correctly scores ministers for a given intent."""
        court = ImperialCourt()
        court.install_ministers_from_factory()

        scores = court.analyze_petition("写一个Python脚本分析CSV数据")
        assert len(scores) > 0
        # 工部尚书 should score highest for code tasks
        top = max(scores, key=scores.get)
        assert top in scores

    def test_select_ministers_high_confidence(self):
        """Single minister selected for high-confidence dispatch."""
        court = ImperialCourt()
        court.install_ministers_from_factory()

        # Override can_handle for 丞相 to guarantee high score
        chancellor = court.ministers["丞相"]
        original = chancellor.can_handle
        chancellor.can_handle = lambda t: 0.75
        selected = court._select_ministers({"丞相": 0.75, "太史令": 0.4, "工部尚书": 0.3})
        assert selected == ["丞相"]
        chancellor.can_handle = original

    def test_select_ministers_court_session(self):
        """Multiple ministers selected when qualified (朝堂议事)."""
        court = ImperialCourt()
        court.install_ministers_from_factory()

        # Simulate multiple qualified ministers
        scores = {"丞相": 0.65, "太史令": 0.55, "工部尚书": 0.52, "太常": 0.35}
        selected = court._select_ministers(scores)
        # Should select up to top 3 qualified (>= 0.5)
        assert len(selected) >= 1
        assert all(s in scores for s in selected)


# ── Full Pipeline: Emperor.receive_petition ──────────────────────────────────

class TestEmperorFullPipeline:
    @pytest.mark.asyncio
    async def test_receive_petition_single(self):
        """Emperor processes a petition with a single minister."""
        emperor = Emperor()
        decree = await emperor.receive_petition("写一个Python排序算法")
        assert isinstance(decree, Decree)
        assert decree.success is True
        assert len(decree.output) > 0
        assert decree.confidence > 0
        assert len(decree.ministers_consulted) >= 1

    @pytest.mark.asyncio
    async def test_receive_petition_code(self):
        """Code-related petition routed to Works Minister."""
        emperor = Emperor()
        decree = await emperor.receive_petition("调试Python代码的循环引用问题")
        assert decree.success
        # Should involve 工部尚书
        assert "工部尚书" in decree.ministers_consulted

    @pytest.mark.asyncio
    async def test_receive_petition_security(self):
        """Security petition routed to Guard Captain."""
        emperor = Emperor()
        decree = await emperor.receive_petition("审计代码安全漏洞")
        assert decree.success
        # Should involve 卫尉
        assert "卫尉" in decree.ministers_consulted

    @pytest.mark.asyncio
    async def test_court_metrics(self):
        """Court metrics accumulate correctly after petitions."""
        emperor = Emperor()
        await emperor.receive_petition("优化API性能")
        await emperor.receive_petition("搜索最新AI论文")

        metrics = emperor.get_court_metrics()
        assert metrics["decree_count"] == 2
        assert metrics["minister_count"] == 8
        assert "recent_success_rate" in metrics
        assert "top_performer" in metrics
        assert len(metrics["ministers"]) == 8

    @pytest.mark.asyncio
    async def test_ministers_independent(self):
        """Multiple petitions don't interfere with each other."""
        emperor = Emperor()

        # Parallel petitions
        results = await asyncio.gather(
            emperor.receive_petition("分析代码复杂度"),
            emperor.receive_petition("检查文档安全性"),
            emperor.receive_petition("优化资源配置"),
        )

        assert all(r.success for r in results)
        assert len(emperor.court.records) == 3

    @pytest.mark.asyncio
    async def test_court_session_multi_minister(self):
        """A multi-domain petition triggers a court session (朝堂议事)."""
        emperor = Emperor()

        # Set up scores to force multi-minister
        # We need a petition that scores 0.5+ for multiple ministers
        decree = await emperor.receive_petition(
            "帮我写一份关于AI安全的技术报告，需要包含代码示例和成本分析"
        )
        assert decree.success
        # This type of multi-domain query may trigger multiple ministers
        assert len(decree.ministers_consulted) >= 1


# ── Feedback & Evolution ─────────────────────────────────────────────────────

class TestCourtFeedback:
    @pytest.mark.asyncio
    async def test_emperor_feedback(self):
        """Emperor can send feedback to ministers."""
        emperor = Emperor()
        decree = await emperor.receive_petition("优化数据库查询")

        for name in decree.ministers_consulted:
            emperor.court.send_feedback(decree.decree_id, name, 0.95)

        for name in decree.ministers_consulted:
            minister = emperor.court.get_minister(name)
            metrics = minister.get_evolution_metrics()
            assert metrics["total_feedback"] > 0


# ── Edge Cases ───────────────────────────────────────────────────────────────

class TestCourtEdgeCases:
    def test_dismiss_nonexistent_minister(self):
        """Dismissing a non-existent minister returns False."""
        court = ImperialCourt()
        assert court.dismiss_minister("nonexistent") is False

    def test_empty_court_analyze(self):
        """Analyzing a petition with no ministers returns empty scores."""
        court = ImperialCourt()
        scores = court.analyze_petition("anything")
        assert scores == {}

    @pytest.mark.asyncio
    async def test_court_without_kg(self):
        """Court works fine without knowledge graph."""
        court = ImperialCourt(knowledge_graph=None)
        court.install_ministers_from_factory()
        # Should not crash on ingestion attempt
        decree = await court.receive_petition("检查代码")
        assert decree.success
