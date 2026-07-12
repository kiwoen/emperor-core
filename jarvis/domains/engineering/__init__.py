"""
Engineering Domain — code generation, debugging, refactoring, architecture.
"""

from __future__ import annotations

from typing import Any
from jarvis.core.orchestrator import Domain, DomainModule, Intent, TaskResult


DOMAIN = Domain.ENGINEERING

CAPABILITIES = [
    "code_generation", "debugging", "refactoring",
    "git_operations", "architecture_design", "testing", "ci_cd",
]


class DomainModule(DomainModule):
    """Engineering domain handler."""

    domain = Domain.ENGINEERING
    capabilities = CAPABILITIES

    async def handle(self, intent: Intent) -> TaskResult:
        text = intent.raw_text.lower()

        if "写" in text or "生成" in text or "函数" in text:
            data: dict[str, Any] = {"language": "python"}
        elif "bug" in text or "修复" in text or "debug" in text:
            data = {"sandbox": True, "operation": "debug"}
        elif "重构" in text or "refactor" in text:
            data = {"preserve_tests": True, "operation": "refactor"}
        elif "git" in text:
            if "commit" in text:
                data = {"operation": "commit"}
            else:
                data = {"operation": "commit"}
        elif "架构" in text or "architecture" in text or "微服务" in text:
            data = {"deliverables": ["system_diagram", "component_spec", "api_contract"]}
        else:
            data = {"language": "python"}

        return TaskResult(domain=Domain.ENGINEERING, success=True, output=f"[ENGINEERING] Processing: {intent.raw_text}", data=data)
