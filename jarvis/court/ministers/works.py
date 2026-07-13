"""工部尚书 (Minister of Works) — DeepSeek-style code engineering & debugging."""

from __future__ import annotations

import asyncio
from jarvis.court.minister import Edict, Minister, MinisterProfile


class WorksMinister(Minister):
    """The Minister of Works — code generation, debugging, architecture.

    Archetype: DeepSeek-R1 + Cursor
    Strengths: 代码生成、调试修复、架构设计、技术选型、数学推理
    Weaknesses: 文章写作、图像处理
    """

    def __init__(self) -> None:
        profile = MinisterProfile(
            title="工部尚书",
            archetype="DeepSeek-R1 + Cursor",
            domain="code",
            strengths=[
                "code generation", "debugging", "architecture",
                "refactoring", "algorithm", "technical design",
                "代码", "编程", "调试", "架构", "开发", "算法", "重构", "技术",
            ],
            weaknesses=[
                "essay writing", "image processing",
                "文章", "图像",
            ],
            decision_style="decisive",
            quality_score=0.86,
        )
        super().__init__(profile)

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        await asyncio.sleep(0)
        intent = edict.intent
        output = (
            f"[工部·营造录]\n"
            f"奉旨：{intent}\n\n"
            f"微臣详查代码方案如下：\n"
            f"  · 选型建议：Python 3.11 + FastAPI + asyncio；\n"
            f"  · 架构方案：分层微服务，事件驱动总线；\n"
            f"  · 风险提示：并发瓶颈在 I/O 层，建议加异步缓存。\n\n"
            f"如需详细代码，可进一步绘制工程图则。"
        )
        confidence = 0.80
        return output, confidence
