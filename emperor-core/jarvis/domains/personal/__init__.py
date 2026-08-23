"""
Personal Domain — reminders, todos, notes, planning, focus.
"""

from __future__ import annotations

from typing import Any
from jarvis.core.orchestrator import Domain, DomainModule, Intent, TaskResult
from jarvis.core.llm import get_llm


DOMAIN = Domain.PERSONAL

CAPABILITIES = [
    "reminders", "todos", "notes", "planning",
    "focus_timer", "calendar", "journal",
]


class DomainModule(DomainModule):
    """Personal domain handler."""

    domain = Domain.PERSONAL
    capabilities = CAPABILITIES

    async def handle(self, intent: Intent) -> TaskResult:
        text = intent.raw_text.lower()

        if "提醒" in text or "remind" in text:
            data: dict[str, Any] = {"type": "reminder"}
        elif "待办" in text or "todo" in text:
            data = {"type": "todo", "status": "pending"}
        elif "笔记" in text or "记录" in text or "note" in text:
            data = {"type": "note", "note_key": intent.raw_text[:30]}
        elif "计划" in text or "plan" in text or "制定" in text:
            data = {"type": "plan", "time_blocks": ["morning", "afternoon", "evening"]}
        elif "番茄" in text or "pomodoro" in text or "专注" in text:
            data = {"type": "focus", "method": "pomodoro"}
        else:
            data = {"type": "general"}

        llm = get_llm()
        output = await llm.complete(intent.raw_text, domain="personal")
        return TaskResult(domain=Domain.PERSONAL, success=True, output=output, data=data)
