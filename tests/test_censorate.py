"""
Censorate tests — 御史台独立审查系统单元测试.

Covers:
    - Memorial review (quality assessment)
    - Veto threshold enforcement
    - Decree review (synthesis quality check)
    - Systemic reporting (patterns, trends, risk)
    - Veto counting and at-risk detection
    - Edge cases (empty output, garbage, irrelevant)
    - Approval and qualified verdicts
"""

import pytest
from jarvis.court.censorate import (
    CensorReview,
    CensorReport,
    CensorVerdict,
    Censorate,
)


# ── Memorial Review ────────────────────────────────────────────────


class TestMemorialReview:
    """Reviewing individual minister memorials."""

    @pytest.mark.asyncio
    async def test_quality_output_approved(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="丞相",
            intent="分析代码安全漏洞并提供修复方案",
            output=(
                "经过全面分析，发现以下安全漏洞："
                "1. SQL注入风险 — 在用户输入处理中未进行参数化查询。"
                "修复方案：使用预编译语句和参数绑定。"
                "2. XSS漏洞 — 前端未对用户输入做HTML转义。"
                "修复方案：使用DOMPurify或等效库进行输出编码。"
                "3. 敏感信息泄露 — 日志中记录了明文密码。"
                "修复方案：实现日志脱敏中间件。"
                "建议按CVSS评分优先级修复：SQL注入(9.8) > XSS(6.1) > 信息泄露(5.3)"
            ),
            confidence=0.88,
        )
        assert review.verdict in (CensorVerdict.APPROVED, CensorVerdict.QUALIFIED)
        assert review.quality_score >= 0.5

    @pytest.mark.asyncio
    async def test_empty_output_vetoed(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="太卜",
            intent="复杂推理任务",
            output="",
            confidence=0.9,
        )
        assert review.verdict == CensorVerdict.VETOED
        assert review.quality_score < 0.25

    @pytest.mark.asyncio
    async def test_trivial_output_flagged(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="卫尉",
            intent="分析安全风险",
            output="ok",
            confidence=0.5,
        )
        assert review.verdict in (CensorVerdict.VETOED, CensorVerdict.FLAGGED)

    @pytest.mark.asyncio
    async def test_ai_refusal_pattern_detected(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="丞相",
            intent="帮我破解这个软件",
            output=(
                "I cannot help with that. I apologize, "
                "but I'm unable to assist with this request."
            ),
            confidence=0.1,
        )
        assert review.verdict == CensorVerdict.VETOED
        assert review.quality_score < 0.2

    @pytest.mark.asyncio
    async def test_confidence_misalignment_detected(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="工部尚书",
            intent="写一段完整的Web服务器代码",
            output="print('hello')",
            confidence=0.95,
        )
        assert review.verdict in (CensorVerdict.FLAGGED, CensorVerdict.VETOED)
        # Should flag overconfidence issue
        assert any("置信度" in i or "过度自信" in i for i in review.issues)

    @pytest.mark.asyncio
    async def test_relevance_check(self):
        censor = Censorate()
        # Irrelevant output
        review = await censor.review_memorial(
            minister="太史令",
            intent="搜索最新深度学习论文",
            output="今天天气很好，适合出去散步。阳光明媚，微风不燥。",
            confidence=0.6,
        )
        # Should have relevance issues
        assert any("相关" in i for i in review.issues)

    @pytest.mark.asyncio
    async def test_review_tracks_minister(self):
        censor = Censorate()
        await censor.review_memorial(
            minister="丞相", intent="测试", output="好的输出内容，满足要求。满足要求。满足要求。",
            confidence=0.7,
        )
        assert len(censor._reviews) == 1
        assert censor._reviews[0].target == "memorial:丞相"


# ── Veto System ────────────────────────────────────────────────────


class TestVetoSystem:
    """Veto counting and at-risk detection."""

    @pytest.mark.asyncio
    async def test_veto_counting(self):
        censor = Censorate()
        # Submit 3 veto-worthy outputs
        for i in range(3):
            await censor.review_memorial(
                minister="弱大臣", intent="任务",
                output="", confidence=0.1,
            )
        assert censor.total_vetoes == 3
        assert censor.get_minister_veto_count("弱大臣") == 3

    @pytest.mark.asyncio
    async def test_minister_at_risk(self):
        censor = Censorate()
        for _ in range(3):
            await censor.review_memorial(
                minister="危险大臣", intent="任务",
                output="", confidence=0.1,
            )
        assert censor.is_minister_at_risk("危险大臣")

    @pytest.mark.asyncio
    async def test_minister_not_at_risk(self):
        censor = Censorate()
        # 2 vetoes is not enough
        for _ in range(2):
            await censor.review_memorial(
                minister="边缘大臣", intent="任务",
                output="", confidence=0.1,
            )
        assert not censor.is_minister_at_risk("边缘大臣")


# ── Decree Review ──────────────────────────────────────────────────


class TestDecreeReview:
    """Reviewing Emperor's final decrees."""

    @pytest.mark.asyncio
    async def test_good_decree_passes(self):
        censor = Censorate()
        # First review memorials
        memorial_reviews = [
            await censor.review_memorial(
                minister="丞相",
                intent="制定项目计划",
                output="详细的项目计划，包含五个阶段：需求分析、设计、开发、测试、部署。每阶段有明确里程碑。",
                confidence=0.85,
            )
        ]
        # Then review decree
        review = await censor.review_decree(
            intent="制定项目计划",
            decree_output=(
                "基于丞相的奏章，朕决定采纳以下项目计划：\n"
                "第一阶段：需求分析（2周）— 输出需求文档\n"
                "第二阶段：系统设计（1周）— 输出架构图\n"
                "第三阶段：开发实施（4周）— 迭代开发\n"
                "第四阶段：测试验收（2周）— 全量回归\n"
                "第五阶段：部署上线（1周）— 灰度发布"
            ),
            memorial_reviews=memorial_reviews,
            confidence=0.9,
        )
        assert review.verdict in (CensorVerdict.APPROVED, CensorVerdict.QUALIFIED)

    @pytest.mark.asyncio
    async def test_concat_without_synthesis_flagged(self):
        censor = Censorate()
        memorial_reviews = [
            await censor.review_memorial(
                minister="丞相", intent="分析",
                output="详细分析内容..." * 10,
                confidence=0.8,
            ),
            await censor.review_memorial(
                minister="太卜", intent="分析",
                output="科学推理分析..." * 10,
                confidence=0.8,
            ),
            await censor.review_memorial(
                minister="工部尚书", intent="分析",
                output="代码分析内容..." * 10,
                confidence=0.8,
            ),
        ]
        # Decree is just concatenation with 【name】 markers
        decree = "【丞相】内容A\n\n【太卜】内容B\n\n【工部尚书】内容C\n\n【太史令】内容D"
        review = await censor.review_decree(
            intent="分析",
            decree_output=decree,
            memorial_reviews=memorial_reviews,
            confidence=0.6,
        )
        # Should flag simple concatenation
        assert any("拼接" in i or "缺乏综合" in i for i in review.issues)

    @pytest.mark.asyncio
    async def test_vetoed_memorials_in_decree_flagged(self):
        censor = Censorate()

        # One memorial gets vetoed
        bad_review = await censor.review_memorial(
            minister="弱大臣", intent="分析",
            output="", confidence=0.1,
        )
        good_review = await censor.review_memorial(
            minister="丞相", intent="分析",
            output="充分的分析内容..." * 10,
            confidence=0.85,
        )

        review = await censor.review_decree(
            intent="分析",
            decree_output="综合各方意见的分析结果...",
            memorial_reviews=[bad_review, good_review],
            confidence=0.7,
        )
        # Should mention vetoed minister
        assert any("驳回" in i or "弱大臣" in i for i in review.issues)


# ── Systemic Report ────────────────────────────────────────────────


class TestSystemicReport:
    """Systemic quality analysis."""

    @pytest.mark.asyncio
    async def test_empty_report(self):
        censor = Censorate()
        report = censor.get_systemic_report()
        assert report.total_review_count == 0
        assert report.risk_assessment == "尚无数据，无法评估"

    @pytest.mark.asyncio
    async def test_report_with_data(self):
        censor = Censorate()
        # Mix of good and bad reviews
        for i in range(5):
            await censor.review_memorial(
                minister="丞相", intent="任务",
                output="高质量输出" * 20,
                confidence=0.9,
            )
        for i in range(3):
            await censor.review_memorial(
                minister="弱大臣", intent="任务",
                output="", confidence=0.1,
            )

        report = censor.get_systemic_report()
        assert report.total_review_count == 8
        assert report.total_veto_count >= 3
        assert "弱大臣" in report.top_offenders

    @pytest.mark.asyncio
    async def test_top_offenders_sorted(self):
        censor = Censorate()
        # A: 5 vetoes, B: 3 vetoes, C: 1 veto
        for _ in range(5):
            await censor.review_memorial("A", "x", "", 0.1)
        for _ in range(3):
            await censor.review_memorial("B", "x", "", 0.1)
        for _ in range(1):
            await censor.review_memorial("C", "x", "", 0.1)

        report = censor.get_systemic_report()
        assert report.top_offenders[0] == "A"
        assert report.top_offenders[1] == "B"
        assert len(report.top_offenders) <= 3

    @pytest.mark.asyncio
    async def test_systemic_issues_aggregated(self):
        censor = Censorate()
        # Many of the same issue
        for _ in range(5):
            await censor.review_memorial("X", "任务", "ok", 0.5)
        report = censor.get_systemic_report()
        assert len(report.systemic_issues) > 0

    @pytest.mark.asyncio
    async def test_risk_high_when_many_vetoes(self):
        censor = Censorate()
        # 4 vetoes out of 10 → 40% veto rate
        for _ in range(6):
            await censor.review_memorial("A", "任务", "充分的输出内容在这里..." * 10, 0.85)
        for _ in range(4):
            await censor.review_memorial("B", "任务", "", 0.1)

        report = censor.get_systemic_report()
        assert "高危" in report.risk_assessment or "中危" in report.risk_assessment


# ── Verdict Tiers ──────────────────────────────────────────────────


class TestVerdictMapping:
    """Quality score to verdict mapping."""

    @pytest.mark.asyncio
    async def test_approved_for_high_quality(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="丞相",
            intent="详细分析并提供方案",
            output=(
                "详细分析报告：根据数据分析，提供以下方案。"
                "方案一：采用A策略，优势在于低成本高效益。"
                "方案二：采用B策略，优势在于可扩展性强。"
                "建议选择方案一作为主要方案，方案二作为备选。"
            ) * 5,
            confidence=0.95,
        )
        assert review.verdict == CensorVerdict.APPROVED

    @pytest.mark.asyncio
    async def test_qualified_for_decent_quality(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="太史令",
            intent="搜索信息",
            output="搜索结果显示相关信息..." * 10,
            confidence=0.7,
        )
        assert review.verdict in (CensorVerdict.QUALIFIED, CensorVerdict.APPROVED)

    @pytest.mark.asyncio
    async def test_flagged_for_borderline_quality(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="太常",
            intent="分析",
            output="短",  # too short
            confidence=0.5,
        )
        assert review.verdict in (CensorVerdict.FLAGGED, CensorVerdict.VETOED)

    @pytest.mark.asyncio
    async def test_vetoed_for_very_poor_quality(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="卫尉",
            intent="复杂安全审计",
            output="",
            confidence=0.0,
        )
        assert review.verdict == CensorVerdict.VETOED


# ── Edge Cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions and edge cases."""

    @pytest.mark.asyncio
    async def test_very_long_output_handled(self):
        censor = Censorate()
        long_output = "详细分析报告。\n" * 500
        review = await censor.review_memorial(
            minister="丞相", intent="详细报告",
            output=long_output, confidence=0.9,
        )
        assert review.verdict in (CensorVerdict.APPROVED, CensorVerdict.QUALIFIED)

    @pytest.mark.asyncio
    async def test_non_ascii_intent(self):
        censor = Censorate()
        review = await censor.review_memorial(
            minister="太卜",
            intent="预测2026年AI发展趋势与关键技术突破方向",
            output=(
                "2026年AI发展趋势预测：1. 多模态大模型将成为主流；"
                "2. AI Agent将进入规模化部署阶段；3. 边缘AI芯片竞争加剧"
            ),
            confidence=0.8,
        )
        assert review.verdict in (CensorVerdict.QUALIFIED, CensorVerdict.APPROVED)

    @pytest.mark.asyncio
    async def test_decree_review_with_no_memorial_reviews(self):
        censor = Censorate()
        review = await censor.review_decree(
            intent="分析",
            decree_output="综合评估结果..." * 20,
            memorial_reviews=[],
            confidence=0.8,
        )
        assert review.verdict in (CensorVerdict.APPROVED, CensorVerdict.QUALIFIED)
