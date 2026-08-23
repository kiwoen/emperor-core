"""
Finance Domain — stock analysis, budgeting, crypto tracking, portfolio.
"""

from __future__ import annotations

from typing import Any
from jarvis.core.orchestrator import Domain, DomainModule, Intent, TaskResult
from jarvis.core.llm import get_llm


DOMAIN = Domain.FINANCE

CAPABILITIES = [
    "stock_analysis", "budget_planning", "crypto_tracking",
    "portfolio_analysis", "market_research", "tax_estimation",
]


class DomainModule(DomainModule):
    """Finance domain handler."""

    domain = Domain.FINANCE
    capabilities = CAPABILITIES

    async def handle(self, intent: Intent) -> TaskResult:
        text = intent.raw_text.lower()

        if "a股" in text or "股票" in text or "走势" in text:
            data: dict[str, Any] = {"exchange": "SSE_SZSE", "analysis_type": "technical"}
        elif "预算" in text or "budget" in text:
            data = {"method": "50_30_20", "period": "monthly"}
        elif "比特币" in intent.raw_text or "以太坊" in intent.raw_text or "crypto" in text:
            assets = []
            if "比特币" in intent.raw_text or "bitcoin" in text:
                assets.append("bitcoin")
            if "以太坊" in intent.raw_text or "ethereum" in text:
                assets.append("ethereum")
            data = {"assets": assets}
        elif "投资组合" in text or "portfolio" in text:
            data = {"metrics": ["sharpe", "volatility", "drawdown", "returns", "beta"]}
        else:
            data = {"exchange": "SSE_SZSE"}

        llm = get_llm()
        output = await llm.complete(intent.raw_text, domain="finance")
        return TaskResult(domain=Domain.FINANCE, success=True, output=output, data=data)
