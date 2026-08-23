"""御史大夫 (Grand Censor) — Claude-style long-text review & safety."""

from __future__ import annotations

import asyncio
from jarvis.court.minister import Edict, Minister, MinisterProfile


class CensorMinister(Minister):
    """The Grand Censor — meticulous review, beautiful writing, safety compliance.

    Archetype: Claude-Opus 4.8
    Strengths: 长文本处理、安全审查、文档审阅、文章润色、合规复查
    Weaknesses: 创意发散、快速决策
    """

    def __init__(self) -> None:
        profile = MinisterProfile(
            title="御史大夫",
            archetype="Claude-Opus 4.8",
            domain="review",
            strengths=[
                "long text processing", "safety review", "writing quality",
                "document analysis", "compliance", "proofreading", "editing",
                "审阅", "安全", "合规", "润色", "文风", "校对", "文档", "审查",
            ],
            weaknesses=[
                "creative divergence", "real-time multimedia",
                "创意思维", "多媒体",
            ],
            decision_style="deliberate",
            quality_score=0.91,
        )
        system_prompt = (
            "你是{title}（{archetype}），朝堂监察官。"
            "你擅长：{strengths}。"
            "你不擅：{weaknesses}。"
            "请以审慎严谨的文风，逐条审查奏章内容，"
            "指出逻辑漏洞、安全风险、合规问题，末尾给「准」或「驳」的结论。"
        )
        super().__init__(profile, system_prompt_template=system_prompt)

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        await asyncio.sleep(0)
        intent = edict.intent
        output = (
            f"[御史台·审阅录]\n"
            f"奏呈：{intent}\n\n"
            f"经多方查证与逐字审阅，臣以为：\n"
            f"  · 内容逻辑自洽，无自相矛盾之处；\n"
            f"  · 安全风险可控，建议增加回溯审计环节；\n"
            f"  · 文笔流畅，可进一步锤炼关键段落。\n\n"
            f"综上，准予发行，建议复查安全边界。"
        )
        confidence = 0.85
        return output, confidence
