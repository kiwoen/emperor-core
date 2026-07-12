"""
Health Domain — exercise, sleep, diet, meditation.
"""

from __future__ import annotations

from typing import Any
from jarvis.core.orchestrator import Domain, DomainModule, Intent, TaskResult


DOMAIN = Domain.HEALTH

CAPABILITIES = [
    "exercise_plan", "sleep_analysis", "diet_plan",
    "meditation_guide", "health_tracking", "workout_routine",
]


class DomainModule(DomainModule):
    """Health domain handler."""

    domain = Domain.HEALTH
    capabilities = CAPABILITIES

    async def handle(self, intent: Intent) -> TaskResult:
        text = intent.raw_text.lower()

        if "跑" in text or "运动" in text or "锻炼" in text:
            if "跑步" in intent.raw_text:
                data: dict[str, Any] = {"type": "跑步"}
            else:
                data = {"type": "运动"}
        elif "睡眠" in text or "sleep" in text:
            data = {"metrics": ["duration", "deep_sleep", "rem_sleep", "sleep_quality"]}
        elif "食谱" in text or "diet" in text or "减脂" in text or "吃" in text:
            restrictions = []
            if "素食" in text or "vegan" in text:
                restrictions.append("vegan")
            if "减脂" in text or "fat" in text:
                restrictions.append("low_fat")
            if not restrictions:
                restrictions.append("balanced")
            data = {"restrictions": restrictions}
        elif "冥想" in text or "meditation" in text:
            data = {"type": "guided_breathing"}
        else:
            data = {"type": "general"}

        return TaskResult(domain=Domain.HEALTH, success=True, output=f"[HEALTH] Processing: {intent.raw_text}", data=data)
