"""
JARVIS Cost-Aware Multi-Model Router.

Zero-cost complexity classification using pure rule matching.
Routes prompts to cheap/standard/premium model tiers to minimize API spend.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("jarvis.router")


# ── Model Registry ──────────────────────────────────────────────────

MODEL_REGISTRY: dict[str, list[str]] = {
    "cheap": ["gpt-4o-mini", "claude-haiku"],
    "standard": ["gpt-4o", "claude-sonnet"],
    "premium": ["gpt-4o", "claude-opus"],
}

# Estimated per-1K-token costs (USD, approximate for illustration)
_COST_PER_1K: dict[str, float] = {
    "cheap": 0.00015,
    "standard": 0.003,
    "premium": 0.015,
}

# ── Complexity cues (zero-LLM, pure regex) ─────────────────────────

# Cues that suggest a cheap model is sufficient
_CHEAP_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(你好|hello|hi|hey)\b", re.IGNORECASE),
    re.compile(r"^(谢谢|thanks|thank you)\b", re.IGNORECASE),
    re.compile(r"^(再见|bye|goodbye)\b", re.IGNORECASE),
    re.compile(r"翻译|translate|translation", re.IGNORECASE),
    re.compile(r"天气|weather", re.IGNORECASE),
    re.compile(r"几点了|什么时间|what time|current time", re.IGNORECASE),
    re.compile(r"今天.*星期|what day", re.IGNORECASE),
    re.compile(r"定义|definition|what is\b", re.IGNORECASE),
    re.compile(r"简单.*问题|simple question", re.IGNORECASE),
]

# Cues that require a premium model
_PREMIUM_PATTERNS: list[re.Pattern] = [
    re.compile(r"(实现|编写|写.*代码|code|implement|program|debug|重构|refactor)", re.IGNORECASE),
    re.compile(r"(算法|algorithm|复杂度|complexity|证明|proof|定理|theorem)", re.IGNORECASE),
    re.compile(r"(数学|math|方程|equation|微积分|calculus|线性代数)", re.IGNORECASE),
    re.compile(r"(规划|plan|多步|multi[- ]?step|推理|reason|逻辑|logic)", re.IGNORECASE),
    re.compile(r"(长篇|长文档|长文|全文|完整.*分析|完整.*总结|comprehensive)", re.IGNORECASE),
    re.compile(r"(论文|paper|research|研究.*分析|深度|deep.*analysis)", re.IGNORECASE),
    re.compile(r"(架构|architecture|设计.*系统|system.*design)", re.IGNORECASE),
    re.compile(r"(优化|optimize|调优|tuning|性能.*分析)", re.IGNORECASE),
    re.compile(r"(安全.*审查|漏洞.*分析|安全.*评估|security.*audit)", re.IGNORECASE),
    re.compile(r"(法律|legal|合同.*审查|contract.*review)", re.IGNORECASE),
]

# ── Router Implementation ─────────────────────────────────────────


@dataclass
class RouterResult:
    """Result of a routing decision."""
    model_id: str
    tier: str
    estimated_cost: float


class ModelRouter:
    """Cost-aware router that classifies prompt complexity without LLM calls.

    Usage::

        router = ModelRouter()
        result = router.route("Hello!", "general")
        # → RouterResult(model_id="gpt-4o-mini", tier="cheap", estimated_cost=0.00015)
    """

    def __init__(self) -> None:
        self.total_requests: int = 0
        self.requests_by_tier: dict[str, int] = {
            "cheap": 0,
            "standard": 0,
            "premium": 0,
        }
        self.estimated_cost_saved: float = 0.0
        self._total_estimated_cost: float = 0.0
        logger.info("ModelRouter initialized with 3 tiers: cheap/standard/premium")

    def estimate_complexity(self, prompt: str, domain: str = "general") -> str:
        """Classify prompt complexity as 'cheap', 'standard', or 'premium'.

        Pure rule-based — zero API calls.

        Args:
            prompt: The user prompt text.
            domain: Domain identifier (general, code, math, etc.).

        Returns:
            One of 'cheap', 'standard', 'premium'.
        """
        prompt_len = len(prompt)

        # 1. Check premium cues first (highest priority)
        for pat in _PREMIUM_PATTERNS:
            if pat.search(prompt):
                return "premium"

        # 2. Domain-based heuristics
        if domain in ("code", "math", "security", "legal"):
            return "premium"

        # 3. Check cheap cues
        for pat in _CHEAP_PATTERNS:
            if pat.search(prompt):
                return "cheap"

        # 4. Short text + simple domain → cheap
        if prompt_len < 50:
            return "cheap"

        # 5. Default → standard
        return "standard"

    def route(
        self,
        prompt: str,
        domain: str = "general",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> RouterResult:
        """Route a prompt to the optimal model tier.

        Args:
            prompt: The user prompt.
            domain: Domain identifier.
            temperature: (unused, for interface compatibility)
            max_tokens: (unused, for interface compatibility)

        Returns:
            RouterResult with model_id, tier, and estimated cost.
        """
        tier = self.estimate_complexity(prompt, domain)
        model_id = MODEL_REGISTRY[tier][0]  # pick first model in tier
        cost = _COST_PER_1K[tier]

        # Update tracking
        self.total_requests += 1
        self.requests_by_tier[tier] += 1
        self._total_estimated_cost += cost

        # Cost saved vs always using premium
        premium_cost = _COST_PER_1K["premium"]
        self.estimated_cost_saved += (premium_cost - cost)

        return RouterResult(
            model_id=model_id,
            tier=tier,
            estimated_cost=cost,
        )

    def report(self) -> dict:
        """Return a statistics summary.

        Returns:
            Dict with total_requests, requests_by_tier, estimated_cost_saved,
            savings_percent, tier_distribution.
        """
        if self.total_requests == 0:
            return {
                "total_requests": 0,
                "requests_by_tier": {"cheap": 0, "standard": 0, "premium": 0},
                "estimated_cost_saved": 0.0,
                "savings_percent": 0.0,
                "tier_distribution": {"cheap": 0, "standard": 0, "premium": 0},
            }

        # Percent saved: cost_saved / total_premium_cost * 100
        total_premium_cost = self.total_requests * _COST_PER_1K["premium"]
        savings_percent = (
            (self.estimated_cost_saved / total_premium_cost * 100)
            if total_premium_cost > 0
            else 0.0
        )

        tier_distribution = {
            tier: round(count / self.total_requests * 100, 1)
            for tier, count in self.requests_by_tier.items()
        }

        return {
            "total_requests": self.total_requests,
            "requests_by_tier": dict(self.requests_by_tier),
            "estimated_cost_saved": round(self.estimated_cost_saved, 6),
            "savings_percent": round(savings_percent, 1),
            "tier_distribution": tier_distribution,
        }
