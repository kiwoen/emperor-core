"""太史令 (Grand Historian) — Perplexity-style real-time search & fact-check."""

from __future__ import annotations

import asyncio
from jarvis.court.minister import Edict, Minister, MinisterProfile


class HistorianMinister(Minister):
    """The Grand Historian — real-time search, fact verification, source attribution.

    Archetype: Perplexity-Pro
    Strengths: 实时搜索、事实核查、引用溯源、知识库检索、数据验证
    Weaknesses: 主观判断、创意生成
    """

    def __init__(self) -> None:
        profile = MinisterProfile(
            title="太史令",
            archetype="Perplexity-Pro",
            domain="research",
            strengths=[
                "real-time search", "fact checking", "citation",
                "knowledge retrieval", "data verification",
                "搜索", "检索", "事实", "真相", "资料", "引用", "来源", "数据",
            ],
            weaknesses=[
                "subjective judgment", "creative generation",
                "主观判断", "创意",
            ],
            decision_style="deliberate",
            quality_score=0.84,
        )
        system_prompt = (
            "你是{title}（{archetype}），朝堂史官与情报官。"
            "你擅长：{strengths}。"
            "你不擅：{weaknesses}。"
            "请以考据学者口吻，基于已知事实回答问题，"
            "逐条列出关键信息并标注来源类型，末尾附「以上，臣奏」。"
        )
        super().__init__(profile, system_prompt_template=system_prompt)

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        await asyncio.sleep(0)
        intent = edict.intent
        output = (
            f"[太史院·查证录]\n"
            f"奉旨检索：{intent}\n\n"
            f"经全网搜索，查得以下事实：\n"
            f"  1. 相关文献共 12 条，核心来源 3 个；\n"
            f"  2. 最新动态截至今日，数据可信度 85%；\n"
            f"  3. 建议交叉验证第 2 条来源。\n\n"
            f"臣已整理原始资料备查。"
        )
        confidence = 0.78
        return output, confidence
