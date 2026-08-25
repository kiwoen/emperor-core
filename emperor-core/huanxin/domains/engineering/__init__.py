"""
Engineering Domain — code generation, debugging, refactoring, architecture.
"""

from __future__ import annotations

from typing import Any
from huanxin.core.orchestrator import Domain, DomainModule, Intent, TaskResult
from huanxin.core.llm import get_llm


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

        # 代码审查（多维加权，离线确定性，不触发 LLM）
        if any(k in text for k in ["审查", "代码评审", "代码检查", "代码审计",
                                    "code review", "review", "audit"]):
            import re as _re
            from huanxin.codex.reviewer import CodeReviewer

            m = _re.search(r"```(?:python|js|ts|go|rust)?\s*\n(.*?)```", intent.raw_text, _re.DOTALL)
            code = m.group(1) if m else intent.raw_text
            report = CodeReviewer().review(code, language=None)
            return TaskResult(
                domain=Domain.ENGINEERING,
                success=True,
                output=CodeReviewer.to_markdown(report),
                data={
                    "operation": "code_review",
                    "language": report.language,
                    "code_type": report.code_type,
                    "overall_score": report.overall_score,
                    "grade": report.grade,
                    "issues": [
                        {
                            "severity": i.severity.value,
                            "dimension": i.dimension.value,
                            "location": i.location,
                            "message": i.message,
                            "suggestion": i.suggestion,
                        }
                        for i in report.prioritized_issues
                    ],
                    "honest_na": report.honest_na,
                },
            )

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

        llm = get_llm()
        output = await llm.complete(intent.raw_text, domain="engineering")
        return TaskResult(domain=Domain.ENGINEERING, success=True, output=output, data=data)
