"""
Research & Analytics Domain.

Handles: web search, paper review, data analysis, competitive intelligence,
literature review, trend analysis, market research, experiment design.
"""

from __future__ import annotations

DOMAIN = "research"

CAPABILITIES = [
    "web_search", "paper_review", "data_analysis",
    "competitive_intelligence", "literature_review", "trend_analysis",
    "market_research", "experiment_design", "synthesis_report",
    "news_aggregation",
]

import json
import logging
from typing import Any

logger = logging.getLogger("jarvis.domain.research")


class DomainModule:
    """Research domain — web search, paper retrieval, synthesis."""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    async def handle(self, intent):
        from jarvis.core.orchestrator import TaskResult, Domain

        action = intent.action
        raw = intent.raw_text
        entities = intent.entities

        handlers = {
            "搜索": self._handle_search,
            "论文": self._handle_paper,
            "趋势": self._handle_trend,
            "分析": self._handle_analysis,
            "研究": self._handle_research,
            "新闻": self._handle_news,
        }

        for keyword, handler in handlers.items():
            if keyword in action or keyword in raw:
                return await handler(raw, entities)

        return TaskResult(
            domain=Domain.RESEARCH,
            success=True,
            output=f"Research intent logged: {raw[:100]}",
            data={"action": action, "entities": entities},
            memory_keys=["research_query"],
        )

    async def _handle_search(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        query = entities.get("query", raw)
        return TaskResult(
            domain=Domain.RESEARCH,
            success=True,
            output=f"Search query compiled: {query[:200]}",
            data={
                "query": query,
                "search_type": "web",
                "sources": self._suggest_sources(raw),
            },
            memory_keys=["research_search"],
        )

    async def _handle_paper(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        topic = entities.get("topic", raw)
        return TaskResult(
            domain=Domain.RESEARCH,
            success=True,
            output=f"Paper search initialized for: {topic[:200]}",
            data={
                "topic": topic,
                "sources": ["arxiv", "semantic_scholar", "google_scholar"],
                "search_params": {"max_results": 10, "sort_by": "relevance"},
            },
            memory_keys=["research_paper"],
        )

    async def _handle_research(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.RESEARCH,
            success=True,
            output=f"Research plan generated for: {raw[:200]}",
            data={
                "phase_1": "literature_review",
                "phase_2": "data_collection",
                "phase_3": "analysis",
                "phase_4": "synthesis",
            },
            memory_keys=["research_topic"],
        )

    async def _handle_analysis(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.RESEARCH,
            success=True,
            output=f"Analysis framework ready for: {raw[:200]}",
            data={
                "methods": ["descriptive", "comparative", "trend"],
                "output_formats": ["report", "visualization", "summary"],
            },
            memory_keys=["research_analysis"],
        )

    async def _handle_trend(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.RESEARCH,
            success=True,
            output=f"Trend analysis initiated: {raw[:200]}",
            data={
                "time_horizon": "1_year",
                "data_sources": ["news", "papers", "patents", "social_media"],
            },
            memory_keys=["research_trend"],
        )

    async def _handle_news(self, raw: str, entities: dict):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.RESEARCH,
            success=True,
            output=f"News aggregation for: {raw[:200]}",
            data={
                "aggregation_type": "timeline",
                "sources": ["rss", "api", "web_scrape"],
            },
            memory_keys=["research_news"],
        )

    def _suggest_sources(self, query: str) -> list[str]:
        """Suggest relevant search sources based on query content."""
        sources = ["web"]
        lower = query.lower()
        if any(w in lower for w in ["论文", "paper", "arxiv", "学术"]):
            sources.extend(["arxiv", "semantic_scholar"])
        if any(w in lower for w in ["代码", "code", "github", "repo"]):
            sources.append("github")
        if any(w in lower for w in ["新闻", "news", "最新"]):
            sources.append("news_api")
        if any(w in lower for w in ["数据", "data", "statistics", "统计"]):
            sources.append("data_catalog")
        return sources
