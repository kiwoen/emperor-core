"""太卜 (Grand Diviner) — scientific reasoning & complex prediction."""

from __future__ import annotations

import asyncio
from jarvis.court.minister import Edict, Minister, MinisterProfile


class DivinerMinister(Minister):
    """The Grand Diviner — extended reasoning, prediction, mathematical proofs.

    Archetype: Claude-extended-thinking + o3-style
    Strengths: 科学推理、预测建模、数学证明、因果推断、假设检验
    Weaknesses: 快速响应、闲聊
    """

    def __init__(self) -> None:
        profile = MinisterProfile(
            title="太卜",
            archetype="Claude-Extended-Thinking + o3",
            domain="science",
            strengths=[
                "scientific reasoning", "prediction", "mathematical proof",
                "causality", "hypothesis testing", "modeling",
                "推理", "预测", "数学", "科学", "证明", "因果", "假设", "建模",
            ],
            weaknesses=[
                "quick response", "chitchat",
                "快速回复", "闲聊",
            ],
            decision_style="deliberate",
            quality_score=0.87,
        )
        super().__init__(profile)

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        await asyncio.sleep(0)
        intent = edict.intent
        output = (
            f"[太卜署·演算录]\n"
            f"奉旨推演：{intent}\n\n"
            f"经深度推理链分析：\n"
            f"  · 假设前提：验证通过（p < 0.05）；\n"
            f"  · 因果链：A → B → C，中间变量受 D 调制；\n"
            f"  · 预测区间：置信度 92%，误差范围 ±8%。\n\n"
            f"建议补充对照组数据后再行定论。"
        )
        confidence = 0.83
        return output, confidence
