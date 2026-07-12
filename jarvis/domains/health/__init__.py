"""
Health & Wellness Domain.

Handles: fitness tracking, sleep analysis, diet planning, exercise routines,
meditation guidance, vitals monitoring, wellness recommendations.
"""

from __future__ import annotations

DOMAIN = "health"

CAPABILITIES = [
    "fitness_track", "sleep_analyze", "diet_plan",
    "exercise_routine", "meditation_guide", "vitals_monitor",
    "wellness_recommend", "health_report", "symptom_checker",
    "workout_generator",
]

import logging

logger = logging.getLogger("jarvis.domain.health")


class DomainModule:
    """Health domain — fitness, sleep, diet, wellness."""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    async def handle(self, intent):
        from jarvis.core.orchestrator import TaskResult, Domain

        action = intent.action
        raw = intent.raw_text

        handlers = {
            "健身": self._handle_fitness,
            "运动": self._handle_exercise,
            "锻炼": self._handle_exercise,
            "跑步": self._handle_exercise,
            "瑜伽": self._handle_exercise,
            "睡眠": self._handle_sleep,
            "饮食": self._handle_diet,
            "食谱": self._handle_diet,
            "减脂": self._handle_diet,
            "素食": self._handle_diet,
            "冥想": self._handle_meditation,
            "健康": self._handle_health_check,
            "体重": self._handle_weight,
            "心率": self._handle_vitals,
            "卡路里": self._handle_calorie,
        }

        for keyword, handler in handlers.items():
            if keyword in action or keyword in raw:
                return await handler(raw)

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Health intent logged: {raw[:100]}",
            memory_keys=["health_query"],
        )

    async def _handle_fitness(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Fitness plan: {raw[:150]}",
            data={
                "focus": self._detect_focus(raw),
                "level": "intermediate",
                "frequency": "3x_weekly",
            },
            memory_keys=["health_fitness"],
        )

    async def _handle_exercise(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Exercise plan: {raw[:150]}",
            data={
                "type": self._detect_exercise_type(raw),
                "duration": "45_minutes",
                "equipment": "bodyweight_or_basic",
            },
            memory_keys=["health_exercise"],
        )

    async def _handle_sleep(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Sleep analysis: {raw[:150]}",
            data={
                "metrics": ["duration", "quality", "deep_sleep", "rem", "consistency"],
                "target": "7-9_hours",
                "recommendations": True,
            },
            memory_keys=["health_sleep"],
        )

    async def _handle_diet(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Diet plan: {raw[:150]}",
            data={
                "calorie_target": 2000,
                "macros": {"protein": "30%", "carbs": "40%", "fat": "30%"},
                "restrictions": self._detect_restrictions(raw),
            },
            memory_keys=["health_diet"],
        )

    async def _handle_meditation(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Meditation session: {raw[:150]}",
            data={
                "type": "guided_breathing",
                "duration": "10_minutes",
                "ambient": "rain_sounds",
            },
            memory_keys=["health_meditation"],
        )

    async def _handle_health_check(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Health report: {raw[:150]}",
            data={
                "check_items": ["vitals", "activity", "sleep", "stress"],
                "period": "weekly",
                "format": "dashboard",
            },
            memory_keys=["health_check"],
        )

    async def _handle_weight(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Weight tracking: {raw[:150]}",
            data={
                "goal": "maintain_or_lose",
                "tracking_interval": "daily",
                "trend_analysis": True,
            },
            memory_keys=["health_weight"],
        )

    async def _handle_vitals(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Vitals monitoring: {raw[:150]}",
            data={
                "metrics": ["heart_rate", "blood_pressure", "spo2", "temperature"],
                "alert_thresholds": "clinical_standard",
            },
            memory_keys=["health_vitals"],
        )

    async def _handle_calorie(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.HEALTH,
            success=True,
            output=f"Calorie tracking: {raw[:150]}",
            data={
                "method": "meal_logging",
                "database": "usda_standard",
                "goal": "maintenance",
            },
            memory_keys=["health_calorie"],
        )

    def _detect_focus(self, raw: str) -> str:
        lower = raw.lower()
        areas = ["strength", "cardio", "flexibility", "endurance", "weight_loss", "muscle_gain"]
        for a in areas:
            if a in lower:
                return a
        return "general_fitness"

    def _detect_exercise_type(self, raw: str) -> str:
        lower = raw.lower()
        types = ["跑步", "游泳", "瑜伽", "举重", "有氧", "hiit", "拉伸", "骑行"]
        for t in types:
            if t in lower or t in raw:
                return t
        return "mixed"

    def _detect_restrictions(self, raw: str) -> list[str]:
        restrictions = []
        lower = raw.lower()
        if any(w in lower for w in ["素食", "vegan"]):
            restrictions.append("vegan")
        if any(w in lower for w in ["无麸质", "gluten_free"]):
            restrictions.append("gluten_free")
        if any(w in lower for w in ["低碳", "low_carb"]):
            restrictions.append("low_carb")
        if any(w in lower for w in ["高蛋白", "high_protein"]):
            restrictions.append("high_protein")
        return restrictions or ["none"]
