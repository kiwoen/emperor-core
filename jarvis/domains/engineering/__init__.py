"""
Engineering & Development Domain.

Handles: code generation, debugging, architecture design, git operations,
code review, testing, documentation, deployment scripts, package management.
"""

from __future__ import annotations

DOMAIN = "engineering"

CAPABILITIES = [
    "code_generation", "code_review", "debugging",
    "architecture_design", "git_operations", "testing",
    "documentation", "deployment", "package_management",
    "refactoring", "api_design",
]

import json
import logging
from typing import Any

logger = logging.getLogger("jarvis.domain.engineering")


class DomainModule:
    """Engineering domain — code generation, review, git operations."""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    async def handle(self, intent):
        from jarvis.core.orchestrator import TaskResult, Domain

        action = intent.action
        raw = intent.raw_text
        entities = intent.entities

        handlers = {
            "修复": self._handle_debug,
            "debug": self._handle_debug,
            "写": self._handle_code_gen,
            "生成": self._handle_code_gen,
            "代码": self._handle_code_gen,
            "重构": self._handle_refactor,
            "审查": self._handle_review,
            "测试": self._handle_test,
            "架构": self._handle_architecture,
            "Git": self._handle_git,
            "git": self._handle_git,
            "文档": self._handle_docs,
            "API": self._handle_api,
            "部署": self._handle_deploy,
        }

        for keyword, handler in handlers.items():
            if keyword in action or keyword in raw:
                return await handler(raw, entities)

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Engineering intent logged: {raw[:100]}",
            data={"action": action},
            memory_keys=["engineering_query"],
        )

    async def _handle_code_gen(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        lang = self._detect_language(raw)
        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Code generation ready for {raw[:150]}",
            data={
                "language": lang,
                "context": "sandbox_execution",
                "safety_check": "pending",
            },
            memory_keys=["engineering_codegen"],
        )

    async def _handle_debug(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Debug session initiated: {raw[:150]}",
            data={
                "mode": "interactive",
                "steps": ["reproduce", "isolate", "diagnose", "fix", "verify"],
                "sandbox": True,
            },
            memory_keys=["engineering_debug"],
        )

    async def _handle_refactor(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Refactoring plan for: {raw[:150]}",
            data={
                "strategy": "safe_refactor",
                "preserve_tests": True,
                "backup_first": True,
            },
            memory_keys=["engineering_refactor"],
        )

    async def _handle_review(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Code review initiated: {raw[:150]}",
            data={
                "review_focus": ["correctness", "performance", "security", "style"],
                "output_format": "diff_comments",
            },
            memory_keys=["engineering_review"],
        )

    async def _handle_test(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Test plan generated: {raw[:150]}",
            data={
                "test_types": ["unit", "integration", "e2e"],
                "coverage_target": 0.8,
            },
            memory_keys=["engineering_test"],
        )

    async def _handle_architecture(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Architecture design for: {raw[:150]}",
            data={
                "deliverables": ["system_diagram", "component_spec", "api_contract", "data_model"],
                "patterns": ["microservices", "event_driven", "cqrs", "hexagonal"],
            },
            memory_keys=["engineering_arch"],
        )

    async def _handle_git(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Git operation: {raw[:150]}",
            data={
                "operation": self._detect_git_op(raw),
                "safety": "check_before_push",
            },
            memory_keys=["engineering_git"],
        )

    async def _handle_docs(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Documentation generation: {raw[:150]}",
            data={
                "formats": ["markdown", "docstring", "openapi", "readme"],
                "audience": "developer",
            },
            memory_keys=["engineering_docs"],
        )

    async def _handle_api(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"API design: {raw[:150]}",
            data={
                "style": "REST",
                "spec": "OpenAPI 3.0",
                "auth": ["jwt", "oauth2", "api_key"],
            },
            memory_keys=["engineering_api"],
        )

    async def _handle_deploy(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.ENGINEERING,
            success=True,
            output=f"Deployment plan: {raw[:150]}",
            data={
                "targets": ["docker", "kubernetes", "serverless"],
                "ci_cd": "github_actions",
                "rollback": "automatic",
            },
            memory_keys=["engineering_deploy"],
        )

    def _detect_language(self, raw: str) -> str:
        lower = raw.lower()
        lang_map = {
            "python": "python", "py": "python",
            "javascript": "javascript", "js": "javascript",
            "typescript": "typescript", "ts": "typescript",
            "rust": "rust",
            "go": "go", "golang": "go",
            "java": "java",
            "c++": "cpp", "cpp": "cpp",
            "c#": "csharp", "csharp": "csharp",
            "sql": "sql",
            "shell": "shell", "bash": "shell",
            "html": "html", "css": "css",
        }
        for token, lang in lang_map.items():
            if token in lower:
                return lang
        return "python"

    def _detect_git_op(self, raw: str) -> str:
        lower = raw.lower()
        for op in ["commit", "push", "pull", "merge", "branch", "rebase", "clone", "stash"]:
            if op in lower:
                return op
        return "status"
