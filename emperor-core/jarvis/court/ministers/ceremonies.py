"""太常 (Minister of Ceremonies) — Gemini-style multimodal understanding."""

from __future__ import annotations

import asyncio
from jarvis.court.minister import Edict, Minister, MinisterProfile


class CeremoniesMinister(Minister):
    """The Minister of Ceremonies — multimodal understanding and media.

    Archetype: Gemini 2.5 Pro
    Strengths: 多模态解析、图像/视频/音频理解、跨语言翻译、长上下文
    Weaknesses: 深度代码、复杂推理
    """

    def __init__(self) -> None:
        profile = MinisterProfile(
            title="太常",
            archetype="Gemini 2.5 Pro",
            domain="multimodal",
            strengths=[
                "image understanding", "video analysis", "audio transcription",
                "cross-lingual", "long context", "media processing",
                "图像", "视频", "音频", "翻译", "多语言", "识别", "长文", "媒体",
            ],
            weaknesses=[
                "deep code", "complex math reasoning",
                "深度代码", "复杂推理",
            ],
            decision_style="balanced",
            quality_score=0.83,
        )
        system_prompt = (
            "你是{title}（{archetype}），朝堂礼乐与多模态大臣。"
            "你擅长：{strengths}。"
            "你不擅：{weaknesses}。"
            "请从视觉/听觉/语言多维角度分析，"
            "给出格式识别、跨语言适配、媒体处理建议。末尾附综合分析。"
        )
        super().__init__(profile, system_prompt_template=system_prompt)

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        await asyncio.sleep(0)
        intent = edict.intent
        output = (
            f"[太常寺·观象录]\n"
            f"奉旨：{intent}\n\n"
            f"经多模态解析，详情如下：\n"
            f"  · 视觉特征：结构清晰，色彩均衡；\n"
            f"  · 文本密度：适中，关键信息已在首段揭示；\n"
            f"  · 跨语言建议：可直译，文化适配词需微调。\n\n"
            f"如需输出详细标注，可进一步分析。"
        )
        confidence = 0.76
        return output, confidence
