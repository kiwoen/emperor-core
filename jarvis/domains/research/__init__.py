"""
Research Domain — academic search, paper discovery, trend analysis.
"""

from __future__ import annotations

from typing import Any
from jarvis.core.orchestrator import Domain, DomainModule, Intent, TaskResult


DOMAIN = Domain.RESEARCH

CAPABILITIES = [
    "academic_search", "paper_discovery", "trend_analysis",
    "research_framework", "citation_lookup", "literature_review",
]


class DomainModule(DomainModule):
    """Research domain handler."""

    domain = Domain.RESEARCH
    capabilities = CAPABILITIES

    async def handle(self, intent: Intent) -> TaskResult:
        text = intent.raw_text.lower()

        if "搜索" in text or "search" in text:
            data: dict[str, Any] = {"search_type": "academic", "query": intent.raw_text}
        elif "论文" in text or "paper" in text:
            data = {"search_type": "paper", "sources": ["arxiv", "scholar.google", "dblp", "semantic_scholar"]}
        elif "趋势" in text or "trend" in text:
            data = {"search_type": "trend", "time_horizon": "last_6_months"}
        elif "研究" in text or "research" in text:
            data = {"search_type": "framework", "phase_1": "literature_review", "phase_2": "methodology_design", "phase_3": "analysis", "phase_4": "report"}
        else:
            data = {"search_type": "general"}

        return TaskResult(domain=Domain.RESEARCH, success=True, output=f"[RESEARCH] Acknowledged: {intent.raw_text}", data=data)
