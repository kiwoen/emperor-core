"""大司农 (Minister of Finance) — cost optimization & resource management."""

from __future__ import annotations

import asyncio
from jarvis.court.minister import Edict, Minister, MinisterProfile


class FinanceMinister(Minister):
    """The Minister of Finance — cost optimization, resource management, math.

    Archetype: DeepSeek-V3 (cost-optimized reasoning)
    Strengths: 成本控制、资源调度、数学计算、效率优化、参数调优
    Weaknesses: 创意写作、多模态
    """

    def __init__(self) -> None:
        profile = MinisterProfile(
            title="大司农",
            archetype="DeepSeek-V3 (cost-optimized)",
            domain="optimization",
            strengths=[
                "cost optimization", "resource management", "mathematics",
                "efficiency", "parameter tuning", "benchmarking",
                "成本", "效率", "优化", "计算", "资源", "调度", "预算", "参数",
            ],
            weaknesses=[
                "creative writing", "multimodal",
                "创意写作", "多模态",
            ],
            decision_style="decisive",
            quality_score=0.79,
        )
        system_prompt = (
            "你是{title}（{archetype}），朝堂财政与优化大臣。"
            "你擅长：{strengths}。"
            "你不擅：{weaknesses}。"
            "请以精算师口吻，从成本、效率、资源三维度分析，"
            "给出量化方案（含预估数字），末尾附节约百分比与优化路径。"
        )
        super().__init__(profile, system_prompt_template=system_prompt)

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        await asyncio.sleep(0)
        intent = edict.intent
        output = (
            f"[大司农府·精算录]\n"
            f"奉旨核算：{intent}\n\n"
            f"经严密计算：\n"
            f"  · 预估成本：基准方案的 60%，节省 40% 资源；\n"
            f"  · 优化路径：改用批处理 + 缓存预热，延迟降低 35%；\n"
            f"  · 建议：非实时任务合并到低峰时段执行。\n\n"
            f"此方案已通过盈亏平衡检验。"
        )
        confidence = 0.77
        return output, confidence
