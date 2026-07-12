"""
Personal Assistant Domain.

Handles: scheduling, reminders, email, contacts, notes, todos, communications,
calendar management, task prioritization, daily planning.
"""

from __future__ import annotations

DOMAIN = "personal"

CAPABILITIES = [
    "schedule_management", "reminder_creation", "email_composition",
    "contact_search", "note_taking", "todo_list", "calendar_query",
    "communication_summary", "task_prioritization", "daily_planning",
    "habit_tracking", "focus_timer",
]

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.domain.personal")


class DomainModule:
    """Personal domain — scheduling, notes, reminders, daily planning."""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self._notes_db: dict[str, list[dict]] = {}

    async def handle(self, intent):
        from jarvis.core.orchestrator import TaskResult, Domain

        action = intent.action
        raw = intent.raw_text
        entities = intent.entities

        handlers = {
            "闹钟": self._handle_alarm,
            "提醒": self._handle_reminder,
            "日程": self._handle_schedule,
            "日历": self._handle_calendar,
            "笔记": self._handle_note,
            "便签": self._handle_note,
            "备忘录": self._handle_note,
            "待办": self._handle_todo,
            "任务": self._handle_todo,
            "计划": self._handle_plan,
            "习惯": self._handle_habit,
            "专注": self._handle_focus,
            "邮件": self._handle_email,
            "联系人": self._handle_contacts,
            "总结": self._handle_summary,
        }

        for keyword, handler in handlers.items():
            if keyword in action or keyword in raw:
                return await handler(raw, entities)

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Personal intent logged: {raw[:100]}",
            data={"action": action},
            memory_keys=["personal_query"],
        )

    async def _handle_alarm(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        time_str = entities.get("time", self._extract_time(raw))
        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Alarm set: {time_str or 'pending time confirmation'}",
            data={
                "type": "alarm",
                "time": time_str,
                "repeat": entities.get("repeat", "once"),
                "label": self._extract_label(raw),
            },
            memory_keys=["personal_alarm"],
        )

    async def _handle_reminder(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Reminder created: {raw[:150]}",
            data={
                "type": "reminder",
                "time": entities.get("time", self._extract_time(raw)),
                "message": self._extract_label(raw),
                "priority": entities.get("priority", "normal"),
            },
            memory_keys=["personal_reminder"],
        )

    async def _handle_schedule(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Schedule updated: {raw[:150]}",
            data={
                "action": "add_event",
                "time": entities.get("time", self._extract_time(raw)),
                "duration": entities.get("duration", "60min"),
            },
            memory_keys=["personal_schedule"],
        )

    async def _handle_calendar(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Calendar query: {raw[:150]}",
            data={
                "query_type": "view" if "查看" in raw else "add",
                "date_range": entities.get("range", "today"),
            },
            memory_keys=["personal_calendar"],
        )

    async def _handle_note(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        note_content = entities.get("content", raw)
        note_key = self._extract_label(raw) or "quick_note"
        self._notes_db.setdefault(note_key, []).append({
            "content": note_content,
            "timestamp": self._now_iso(),
        })

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Note saved: '{note_key}'",
            data={
                "note_key": note_key,
                "total_notes": len(self._notes_db.get(note_key, [])),
                "format": "markdown",
            },
            memory_keys=["personal_note"],
        )

    async def _handle_todo(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        task = entities.get("task", self._extract_label(raw) or raw[:80])
        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Todo added: {task}",
            data={
                "task": task,
                "priority": entities.get("priority", "medium"),
                "due": entities.get("due"),
                "status": "pending",
            },
            memory_keys=["personal_todo"],
        )

    async def _handle_plan(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Daily plan generated for: {raw[:150]}",
            data={
                "time_blocks": ["morning_focus", "deep_work", "afternoon_tasks", "review"],
                "method": "time_blocking",
                "flexibility": "moderate",
            },
            memory_keys=["personal_plan"],
        )

    async def _handle_habit(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Habit tracking: {raw[:150]}",
            data={
                "habit": self._extract_label(raw),
                "frequency": entities.get("frequency", "daily"),
                "streak_tracking": True,
            },
            memory_keys=["personal_habit"],
        )

    async def _handle_focus(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Focus session: {raw[:150]}",
            data={
                "method": "pomodoro",
                "work_duration": "25min",
                "break_duration": "5min",
                "cycles": entities.get("cycles", 4),
            },
            memory_keys=["personal_focus"],
        )

    async def _handle_email(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Email drafted: {raw[:150]}",
            data={
                "to": entities.get("to"),
                "subject": self._extract_label(raw),
                "draft_status": "ready_for_review",
            },
            memory_keys=["personal_email"],
        )

    async def _handle_contacts(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Contact lookup: {raw[:150]}",
            data={
                "query": self._extract_label(raw),
                "action": "search",
            },
            memory_keys=["personal_contacts"],
        )

    async def _handle_summary(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.PERSONAL,
            success=True,
            output=f"Summary generated: {raw[:150]}",
            data={
                "scope": entities.get("scope", "today"),
                "format": "bullet_points",
                "include": ["tasks_done", "upcoming", "insights"],
            },
            memory_keys=["personal_summary"],
        )

    def _extract_time(self, raw: str) -> str | None:
        """Extract time mentions from raw text."""
        import re
        patterns = [
            r"(\d{1,2}[:：]\d{2})",  # HH:MM
            r"(早上|上午|中午|下午|晚上|明天)(\d{1,2})点",  # 早晨8点
            r"(\d{1,2})点",  # 8点
        ]
        for pat in patterns:
            m = re.search(pat, raw)
            if m:
                return m.group(0)
        return None

    def _extract_label(self, raw: str) -> str:
        """Extract a short label/title from raw text."""
        # Remove common command prefixes
        prefixes = [
            "设置", "创建", "添加", "新建", "帮我", "提醒我", "闹钟",
            "笔记", "待办", "任务", "日程", "计划",
        ]
        result = raw
        for p in prefixes:
            result = result.replace(p, "")
        return result.strip("，。！？ ,.!?")[:100]

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
