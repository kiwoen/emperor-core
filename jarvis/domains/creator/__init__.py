"""
Creator & Content Generation Domain.

Handles: writing, image generation prompts, document creation,
presentation design, video scripting, art direction, music composition.
"""

from __future__ import annotations

DOMAIN = "creator"

CAPABILITIES = [
    "text_generation", "image_prompt", "document_creation",
    "presentation_design", "video_script", "art_direction",
    "music_composition", "creative_writing", "storytelling",
    "content_strategy", "brand_voice",
]

import logging
from typing import Any

logger = logging.getLogger("jarvis.domain.creator")


class DomainModule:
    """Creator domain — content generation across all media types."""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    async def handle(self, intent):
        from jarvis.core.orchestrator import TaskResult, Domain

        action = intent.action
        raw = intent.raw_text
        entities = intent.entities

        handlers = {
            "写": self._handle_writing,
            "小说": self._handle_writing,
            "故事": self._handle_writing,
            "文章": self._handle_writing,
            "创作": self._handle_writing,
            "生成": self._handle_generate,
            "设计": self._handle_design,
            "海报": self._handle_design,
            "PPT": self._handle_presentation,
            "演示": self._handle_presentation,
            "视频": self._handle_video,
            "脚本": self._handle_script,
            "图片": self._handle_image,
            "画": self._handle_image,
            "音乐": self._handle_music,
            "品牌": self._handle_brand,
            "文案": self._handle_copy,
        }

        for keyword, handler in handlers.items():
            if keyword in action or keyword in raw:
                return await handler(raw, entities)

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Creator intent logged: {raw[:100]}",
            data={"action": action},
            memory_keys=["creator_query"],
        )

    async def _handle_writing(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        genre = self._detect_genre(raw)
        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Writing task initialized: {raw[:150]}",
            data={
                "genre": genre,
                "format": entities.get("format", "markdown"),
                "tone": entities.get("tone", "professional"),
                "length": entities.get("length", "medium"),
            },
            memory_keys=["creator_writing"],
        )

    async def _handle_generate(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Content generation: {raw[:150]}",
            data={
                "content_type": self._detect_content_type(raw),
                "output_format": entities.get("format", "text"),
            },
            memory_keys=["creator_generate"],
        )

    async def _handle_design(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Design task initialized: {raw[:150]}",
            data={
                "design_type": self._detect_design_type(raw),
                "dimensions": entities.get("size", "1920x1080"),
                "style": entities.get("style", "modern"),
                "color_palette": "auto_suggest",
            },
            memory_keys=["creator_design"],
        )

    async def _handle_presentation(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Presentation ready: {raw[:150]}",
            data={
                "slides": entities.get("slides", 10),
                "template": entities.get("template", "professional"),
                "format": "pptx",
            },
            memory_keys=["creator_presentation"],
        )

    async def _handle_video(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Video project: {raw[:150]}",
            data={
                "duration": entities.get("duration", "60s"),
                "aspect_ratio": "16:9",
                "format": entities.get("format", "mp4"),
            },
            memory_keys=["creator_video"],
        )

    async def _handle_script(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Script writing: {raw[:150]}",
            data={"format": "screenplay" if "电影" in raw else "content"},
            memory_keys=["creator_script"],
        )

    async def _handle_image(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Image generation prompt: {raw[:150]}",
            data={
                "prompt_style": "detailed",
                "aspect_ratio": entities.get("ratio", "1:1"),
                "quality": "high",
            },
            memory_keys=["creator_image"],
        )

    async def _handle_music(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Music composition prompt: {raw[:150]}",
            data={
                "genre": self._detect_music_genre(raw),
                "duration": entities.get("duration", "120s"),
                "mood": entities.get("mood", "ambient"),
            },
            memory_keys=["creator_music"],
        )

    async def _handle_brand(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Brand strategy: {raw[:150]}",
            data={
                "deliverables": ["voice_guide", "visual_identity", "messaging_matrix"],
            },
            memory_keys=["creator_brand"],
        )

    async def _handle_copy(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.CREATOR,
            success=True,
            output=f"Copywriting: {raw[:150]}",
            data={
                "copy_type": self._detect_copy_type(raw),
                "tone": entities.get("tone", "persuasive"),
            },
            memory_keys=["creator_copy"],
        )

    def _detect_genre(self, raw: str) -> str:
        lower = raw.lower()
        genres = ["小说", "故事", "诗歌", "散文", "论文", "报告", "邮件", "博客", "推文"]
        for g in genres:
            if g in lower or g in raw:
                return g
        return "article"

    def _detect_content_type(self, raw: str) -> str:
        lower = raw.lower()
        types = ["image", "text", "video", "audio", "document", "presentation", "code"]
        for t in types:
            if t in lower:
                return t
        return "text"

    def _detect_design_type(self, raw: str) -> str:
        lower = raw.lower()
        if any(w in lower for w in ["海报", "poster"]):
            return "poster"
        if any(w in lower for w in ["logo", "图标"]):
            return "logo"
        if any(w in lower for w in ["界面", "ui", "app"]):
            return "ui"
        if any(w in lower for w in ["网页", "web", "网站"]):
            return "web"
        return "graphic"

    def _detect_music_genre(self, raw: str) -> str:
        lower = raw.lower()
        genres = ["classical", "jazz", "electronic", "ambient", "rock", "pop", "lofi"]
        for g in genres:
            if g in lower:
                return g
        return "ambient"

    def _detect_copy_type(self, raw: str) -> str:
        lower = raw.lower()
        types = ["广告", "slogan", "landing", "email", "social", "产品", "品牌"]
        for t in types:
            if t in lower or t in raw:
                return t
        return "marketing"
