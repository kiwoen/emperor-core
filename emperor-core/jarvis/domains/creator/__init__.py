"""
Creator Domain — writing, design, presentations, image prompts.
"""

from __future__ import annotations

from typing import Any
from jarvis.core.orchestrator import Domain, DomainModule, Intent, TaskResult
from jarvis.core.llm import get_llm


DOMAIN = Domain.CREATOR

CAPABILITIES = [
    "writing", "design", "presentation",
    "image_prompt", "video_script", "poetry", "storytelling",
]


class DomainModule(DomainModule):
    """Creator domain handler."""

    domain = Domain.CREATOR
    capabilities = CAPABILITIES

    async def handle(self, intent: Intent) -> TaskResult:
        text = intent.raw_text.lower()

        if "小说" in text or "故事" in text:
            data: dict[str, Any] = {"genre": "小说", "format": "text"}
        elif "海报" in text or "poster" in text:
            data = {"design_type": "poster", "format": "image"}
        elif "ppt" in text or "演示" in text or "幻灯片" in text:
            data = {"format": "pptx", "slides_count": 10}
        elif "画" in text or "插画" in text or "星空" in text:
            data = {"prompt_style": "detailed", "format": "image"}
        elif "诗" in text or "poetry" in text:
            data = {"genre": "诗歌", "format": "text"}
        else:
            data = {"genre": "通用", "format": "text"}

        llm = get_llm()
        output = await llm.complete(intent.raw_text, domain="creator")
        return TaskResult(domain=Domain.CREATOR, success=True, output=output, data=data)
