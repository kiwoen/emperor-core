"""丞相 (Prime Minister) — GPT-style general reasoning & writing."""

from __future__ import annotations

import asyncio
from jarvis.court.minister import Edict, Minister, MinisterProfile


class ChancellorMinister(Minister):
    """The Prime Minister — comprehensive general-purpose reasoning.

    Archetype: GPT-5 / o-series
    Strengths: 综合能力、推理、写作、代码解释、Auto-reasoning、任务分解
    Weaknesses: 实时事实、深度专业领域
    """

    def __init__(self) -> None:
        profile = MinisterProfile(
            title="丞相",
            archetype="GPT-5 / o-series",
            domain="general",
            strengths=[
                "general reasoning", "writing", "task decomposition",
                "drafting", "planning", "logic", "explanation",
                "推理", "写作", "计划", "总结", "分解", "逻辑", "分析", "解释", "说明",
            ],
            weaknesses=[
                "real-time facts", "deep technical code", "image recognition",
                "实时事实", "图像识别",
            ],
            decision_style="balanced",
            quality_score=0.88,
        )
        super().__init__(profile)

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        # 丞相总览大局，给出综合方案
        await asyncio.sleep(0)  # yield to event loop
        intent = edict.intent

        # Heuristic: produce a structured plan
        output = (
            f"[丞相府·议事录]\n"
            f"圣上垂询：{intent}\n\n"
            f"臣以为，此事宜分三步：\n"
            f"  一、调研情报——令太史令查证相关事实；\n"
            f"  二、拟订方案——由臣与工部尚书协同草拟；\n"
            f"  三、审验合规——御史大夫复核风险。\n\n"
            f"如需深入特定领域，可令相关大臣进言。"
        )
        confidence = 0.82
        suggestions = [
            "可调度太史令检索最新事实",
            "可调度工部尚书处理技术细节",
            "可调度御史大夫做合规审查",
        ]
        memorial = await self._build_result(edict, output, confidence, suggestions)
        return memorial.output, memorial.confidence

    async def _build_result(self, edict, output, confidence, suggestions):
        # helper to construct suggestions list inline
        from jarvis.court.minister import Memorial, MinisterState
        return Memorial(
            edict_id=edict.edict_id,
            minister=self.name,
            state=MinisterState.EXECUTING,
            success=True,
            output=output,
            confidence=confidence,
            suggestions=suggestions,
        )
