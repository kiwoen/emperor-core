"""
Finance & Investment Domain.

Handles: portfolio tracking, market analysis, budgeting, tax planning,
expense management, investment research, crypto tracking, risk assessment.
"""

from __future__ import annotations

DOMAIN = "finance"

CAPABILITIES = [
    "portfolio_track", "market_analyze", "budget_plan",
    "expense_track", "investment_research", "crypto_track",
    "tax_plan", "risk_assess", "stock_screen",
    "dividend_track",
]

import logging

logger = logging.getLogger("jarvis.domain.finance")


class DomainModule:
    """Finance domain — portfolio, market, budget, investment."""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    async def handle(self, intent):
        from jarvis.core.orchestrator import TaskResult, Domain

        action = intent.action
        raw = intent.raw_text

        handlers = {
            "组合": self._handle_portfolio,
            "投资": self._handle_investment,
            "A股": self._handle_stock,
            "走势": self._handle_stock,
            "股票": self._handle_stock,
            "基金": self._handle_fund,
            "预算": self._handle_budget,
            "支出": self._handle_expense,
            "收入": self._handle_income,
            "税务": self._handle_tax,
            "加密": self._handle_crypto,
            "比特币": self._handle_crypto,
            "以太": self._handle_crypto,
            "行情": self._handle_market,
            "市场": self._handle_market,
            "风险": self._handle_risk,
            "理财": self._handle_investment,
            "交易": self._handle_stock,
        }

        for keyword, handler in handlers.items():
            if keyword in action or keyword in raw:
                return await handler(raw)

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Finance intent logged: {raw[:100]}",
            memory_keys=["finance_query"],
        )

    async def _handle_investment(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Investment analysis: {raw[:150]}",
            data={
                "analysis_type": "fundamental",
                "asset_classes": self._detect_asset_classes(raw),
                "horizon": "medium_term",
                "risk_profile": "moderate",
            },
            memory_keys=["finance_investment"],
        )

    async def _handle_stock(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Stock analysis: {raw[:150]}",
            data={
                "metrics": ["price", "pe_ratio", "market_cap", "dividend_yield", "beta"],
                "exchange": self._detect_exchange(raw),
                "timeframe": "1Y",
            },
            memory_keys=["finance_stock"],
        )

    async def _handle_fund(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Fund analysis: {raw[:150]}",
            data={
                "fund_types": ["index", "active", "etf", "mutual"],
                "metrics": ["nav", "expense_ratio", "alpha", "sharpe_ratio"],
            },
            memory_keys=["finance_fund"],
        )

    async def _handle_budget(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Budget plan: {raw[:150]}",
            data={
                "method": "50_30_20",
                "categories": ["essentials", "wants", "savings"],
                "tracking": "monthly",
            },
            memory_keys=["finance_budget"],
        )

    async def _handle_expense(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Expense tracking: {raw[:150]}",
            data={
                "categories": ["food", "transport", "housing", "entertainment", "utilities"],
                "analysis": ["trend", "category_breakdown", "anomaly_detection"],
            },
            memory_keys=["finance_expense"],
        )

    async def _handle_income(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Income tracking: {raw[:150]}",
            data={
                "sources": ["salary", "side_hustle", "investments", "passive"],
                "projection": "12_month_forecast",
            },
            memory_keys=["finance_income"],
        )

    async def _handle_tax(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Tax planning: {raw[:150]}",
            data={
                "focus": "optimization",
                "regime": "personal_income",
                "year": 2026,
            },
            memory_keys=["finance_tax"],
        )

    async def _handle_crypto(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Crypto tracking: {raw[:150]}",
            data={
                "assets": self._detect_crypto_assets(raw),
                "metrics": ["price", "market_cap", "24h_volume", "dominance"],
            },
            memory_keys=["finance_crypto"],
        )

    async def _handle_market(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Market analysis: {raw[:150]}",
            data={
                "indices": ["S&P500", "NASDAQ", "CSI300", "HSI"],
                "sectors": "all",
                "sentiment": True,
            },
            memory_keys=["finance_market"],
        )

    async def _handle_risk(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Risk assessment: {raw[:150]}",
            data={
                "risk_types": ["market", "credit", "liquidity", "operational"],
                "methodology": "VaR_and_stress_test",
                "confidence": "95%",
            },
            memory_keys=["finance_risk"],
        )

    async def _handle_portfolio(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.FINANCE,
            success=True,
            output=f"Portfolio analysis: {raw[:150]}",
            data={
                "metrics": ["allocation", "performance", "sharpe", "max_drawdown"],
                "rebalance_suggestion": True,
            },
            memory_keys=["finance_portfolio"],
        )

    def _detect_asset_classes(self, raw: str) -> list[str]:
        classes = []
        lower = raw.lower()
        for cls_name, tokens in [
            ("stocks", ["股票", "stock"]),
            ("bonds", ["债券", "bond"]),
            ("crypto", ["加密货币", "crypto", "比特币", "以太"]),
            ("real_estate", ["房产", "real_estate"]),
            ("commodities", ["大宗商品", "commodities"]),
        ]:
            if any(t in lower for t in tokens):
                classes.append(cls_name)
        return classes or ["mixed"]

    def _detect_exchange(self, raw: str) -> str:
        lower = raw.lower()
        if any(w in lower for w in ["a股", "沪深", "上海", "深圳"]):
            return "SSE_SZSE"
        if any(w in lower for w in ["港股", "香港"]):
            return "HKEX"
        return "NYSE_NASDAQ"

    def _detect_crypto_assets(self, raw: str) -> list[str]:
        assets = []
        lower = raw.lower()
        mapping = {
            "btc": "bitcoin", "比特币": "bitcoin",
            "eth": "ethereum", "以太": "ethereum",
            "sol": "solana", "bnb": "binance_coin",
        }
        for token, name in mapping.items():
            if token in lower:
                assets.append(name)
        return assets or ["top_10"]
