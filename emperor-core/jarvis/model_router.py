"""SmartRouter — capability-aware model routing with fallback chains.

This module implements P0.4 of the self-evolving-AI plan.  Before this file
existed, ``jarvis/emperor.py`` imported ``jarvis.model_router.SmartRouter``
inside a ``try/except ImportError`` and silently set ``self._smart_router =
None``.  The import *always* failed (the module did not exist), so every
"smart routing" code path was dead while the CHANGELOG advertised the feature
as shipped.  This is the "假的真" (fake-truth) failure mode the P0 work
eliminates.

Design goals
------------
1. **No silent failure.** Everything is deterministic and dependency-free;
   the router never raises on ordinary input.
2. **Explainable.** :meth:`SmartRouter.explain` returns the evidence behind a
   classification so operators can audit routing decisions.
3. **Configurable.** An optional YAML file may override the built-in keyword,
   domain, tier and fallback-chain tables.  A missing / malformed file
   degrades to the built-in defaults *with an explicit warning*.

Usage::

    from jarvis.model_router import SmartRouter, Capability

    router = SmartRouter()
    cap = router.classify("write a python function to sort a list", "code")
    assert cap is Capability.CODE

    tier = router.get_tier_for_capability(cap)          # "premium"
    chain = router.get_fallback_chain_for_tier(tier)    # ["gpt-4o", ...]
"""

from __future__ import annotations

import logging
import os
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("jarvis.model_router")


# ═══════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════


class Capability(str, Enum):
    """Coarse task capability used to pick a model tier and a minister."""

    MATH = "math"
    CODE = "code"
    REASON = "reason"
    RETRIEVE = "retrieve"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, value: Any) -> "Capability":
        """Best-effort conversion of *value* to a :class:`Capability`.

        Never raises — unknown values collapse to :attr:`Capability.UNKNOWN`.
        """
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.UNKNOWN
        text = str(value).strip().lower()
        for member in cls:
            if member.value == text or member.name.lower() == text:
                return member
        return cls.UNKNOWN


class ModelTier(str, Enum):
    """Cost/quality tier a capability is routed to."""

    PREMIUM = "premium"
    STANDARD = "standard"
    ECONOMY = "economy"

    @classmethod
    def coerce(cls, value: Any) -> "ModelTier":
        """Best-effort conversion of *value* to a :class:`ModelTier`."""
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        for member in cls:
            if member.value == text or member.name.lower() == text:
                return member
        return cls.STANDARD


# ═══════════════════════════════════════════════════════════════════
# Built-in routing tables
# ═══════════════════════════════════════════════════════════════════

#: Domain string → capability.  Domains are the ``MinisterGenome.domain``
#: values used throughout the court (``math``, ``code``, ``search`` …).
DEFAULT_DOMAIN_MAP: Dict[str, Capability] = {
    "math": Capability.MATH,
    "arithmetic": Capability.MATH,
    "science": Capability.MATH,
    "finance": Capability.MATH,
    "statistics": Capability.MATH,
    "code": Capability.CODE,
    "coding": Capability.CODE,
    "engineering": Capability.CODE,
    "works": Capability.CODE,
    "devops": Capability.CODE,
    "reason": Capability.REASON,
    "reasoning": Capability.REASON,
    "planning": Capability.REASON,
    "strategy": Capability.REASON,
    "writing": Capability.REASON,
    "chancellor": Capability.REASON,
    "retrieve": Capability.RETRIEVE,
    "retrieval": Capability.RETRIEVE,
    "search": Capability.RETRIEVE,
    "research": Capability.RETRIEVE,
    "history": Capability.RETRIEVE,
    "knowledge": Capability.RETRIEVE,
}

#: Capability → keyword list.  Matching is case-insensitive substring
#: matching; Chinese keywords are included because the court is bilingual.
DEFAULT_KEYWORD_MAP: Dict[Capability, Tuple[str, ...]] = {
    Capability.MATH: (
        "calculate", "compute", "sum of", "product of", "integral",
        "derivative", "equation", "algebra", "probability", "percentage",
        "arithmetic", "solve for",
        "计算", "求和", "方程", "求解", "概率", "积分", "导数", "百分比",
    ),
    Capability.CODE: (
        "code", "function", "class ", "refactor", "compile", "debug",
        "stack trace", "traceback", "unit test", "pytest", "python",
        "javascript", "typescript", "sql", "regex", "api endpoint",
        "代码", "函数", "重构", "报错", "单测", "编译", "调试", "脚本",
    ),
    Capability.RETRIEVE: (
        "search", "look up", "lookup", "find information", "latest news",
        "who is", "what is the capital", "cite", "reference", "document",
        "检索", "搜索", "查找", "查询", "资料", "文献", "最新消息",
    ),
    Capability.REASON: (
        "why", "explain", "compare", "trade-off", "tradeoff", "analyze",
        "analyse", "strategy", "plan", "pros and cons", "step by step",
        "为什么", "解释", "分析", "对比", "权衡", "推理", "方案", "规划",
    ),
}

#: Regex patterns that are strong signals, checked before keyword scoring.
DEFAULT_PATTERN_MAP: Dict[Capability, Tuple[str, ...]] = {
    Capability.MATH: (
        r"\d+\s*[\+\-\*/x×÷^]\s*\d+",   # 17 * 23
        r"\b\d+\s*%\b",                  # 20 %
    ),
    Capability.CODE: (
        r"```",                          # fenced code block
        r"\bdef\s+\w+\s*\(",             # python def
        r"\b(?:import|from)\s+\w+",      # python import
        r"\bSELECT\b.+\bFROM\b",         # SQL
    ),
}

#: Capability → tier.  MATH/CODE need the strongest models; RETRIEVE is
#: cheap and latency-sensitive; UNKNOWN stays on the safe middle tier.
DEFAULT_TIER_MAP: Dict[Capability, ModelTier] = {
    Capability.MATH: ModelTier.PREMIUM,
    Capability.CODE: ModelTier.PREMIUM,
    Capability.REASON: ModelTier.STANDARD,
    Capability.RETRIEVE: ModelTier.ECONOMY,
    Capability.UNKNOWN: ModelTier.STANDARD,
}

#: Tier → ordered fallback chain of model identifiers.
DEFAULT_FALLBACK_CHAINS: Dict[ModelTier, Tuple[str, ...]] = {
    ModelTier.PREMIUM: ("gpt-4o", "claude-3-5-sonnet", "deepseek-r1", "gpt-4o-mini"),
    ModelTier.STANDARD: ("gpt-4o-mini", "deepseek-v3", "claude-3-5-haiku"),
    ModelTier.ECONOMY: ("deepseek-v3", "gpt-4o-mini"),
}


# ═══════════════════════════════════════════════════════════════════
# SmartRouter
# ═══════════════════════════════════════════════════════════════════


class SmartRouter:
    """Classify a task and map it to a model tier + fallback chain.

    Args:
        config_path: Optional path to a YAML file overriding the built-in
            tables.  Supported top-level keys: ``domains``, ``keywords``,
            ``tiers``, ``fallback_chains``.  A missing file is *not* an
            error (defaults are used), but a malformed file logs a warning.

    Attributes:
        config_path: The path the router was constructed with (may be None).
        loaded_from_config: True when a config file was successfully read.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self.config_path: Optional[str] = config_path
        self.loaded_from_config: bool = False

        self._domain_map: Dict[str, Capability] = dict(DEFAULT_DOMAIN_MAP)
        self._keyword_map: Dict[Capability, List[str]] = {
            cap: list(words) for cap, words in DEFAULT_KEYWORD_MAP.items()
        }
        self._pattern_map: Dict[Capability, List[re.Pattern[str]]] = {
            cap: [re.compile(p, re.IGNORECASE) for p in patterns]
            for cap, patterns in DEFAULT_PATTERN_MAP.items()
        }
        self._tier_map: Dict[Capability, ModelTier] = dict(DEFAULT_TIER_MAP)
        self._fallback_chains: Dict[ModelTier, List[str]] = {
            tier: list(models) for tier, models in DEFAULT_FALLBACK_CHAINS.items()
        }

        if config_path:
            self._load_config(config_path)

    # ── Configuration ─────────────────────────────────────────────

    def _load_config(self, config_path: str) -> None:
        """Merge overrides from a YAML config file into the built-in tables.

        Failures are logged (never raised) so a bad config degrades to the
        documented defaults instead of silently disabling routing.
        """
        if not os.path.isfile(config_path):
            logger.warning(
                "[SmartRouter] config not found at %s — using built-in defaults",
                config_path,
            )
            return

        try:
            import yaml  # type: ignore[import-untyped]

            with open(config_path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
        except Exception:
            logger.warning(
                "[SmartRouter] failed to parse %s — using built-in defaults",
                config_path,
                exc_info=True,
            )
            return

        if not isinstance(raw, dict):
            logger.warning(
                "[SmartRouter] %s is not a mapping — using built-in defaults",
                config_path,
            )
            return

        for domain, cap in (raw.get("domains") or {}).items():
            self._domain_map[str(domain).strip().lower()] = Capability.coerce(cap)

        for cap_name, words in (raw.get("keywords") or {}).items():
            cap = Capability.coerce(cap_name)
            if cap is Capability.UNKNOWN:
                continue
            self._keyword_map.setdefault(cap, [])
            self._keyword_map[cap].extend(str(w).lower() for w in (words or []))

        for cap_name, tier in (raw.get("tiers") or {}).items():
            cap = Capability.coerce(cap_name)
            self._tier_map[cap] = ModelTier.coerce(tier)

        for tier_name, models in (raw.get("fallback_chains") or {}).items():
            tier = ModelTier.coerce(tier_name)
            self._fallback_chains[tier] = [str(m) for m in (models or [])]

        self.loaded_from_config = True
        logger.info("[SmartRouter] routing tables loaded from %s", config_path)

    # ── Classification ────────────────────────────────────────────

    def classify(self, prompt: str, domain: str = "general") -> Capability:
        """Classify a task into a :class:`Capability`.

        Resolution order:
            1. Strong regex signal in the prompt (code fence, ``17 * 23`` …).
            2. Keyword scoring over the prompt — highest score wins.
            3. Explicit ``domain`` → capability mapping.
            4. :attr:`Capability.UNKNOWN`.

        Prompt evidence outranks the declared domain on purpose: callers
        frequently pass the default ``"general"`` domain, and a mislabelled
        domain should not defeat an unambiguous prompt.

        Args:
            prompt: The raw user prompt (may be empty).
            domain: Declared task domain (may be empty or ``"general"``).

        Returns:
            The matched capability, never ``None``.
        """
        return self.explain(prompt, domain)["capability"]

    def explain(self, prompt: str, domain: str = "general") -> Dict[str, Any]:
        """Classify *and* return the evidence used, for auditability.

        Returns:
            ``{"capability": Capability, "source": str, "scores": dict,
            "matched_pattern": str, "reason": str}``

        Never raises: any non-string ``prompt`` / ``domain`` is coerced, so a
        classification call can never break the caller's execution path.
        """
        raw = "" if prompt is None else str(prompt)
        text = raw.lower()
        domain_key = "" if domain is None else str(domain).strip().lower()

        # 1. Strong regex signals
        for cap, patterns in self._pattern_map.items():
            for pattern in patterns:
                if pattern.search(raw):
                    return {
                        "capability": cap,
                        "source": "pattern",
                        "scores": {},
                        "matched_pattern": pattern.pattern,
                        "reason": (
                            f"prompt 命中 {cap.value} 强特征正则 "
                            f"{pattern.pattern!r}"
                        ),
                    }

        # 2. Keyword scoring
        scores: Dict[Capability, int] = {}
        for cap, words in self._keyword_map.items():
            hits = sum(1 for word in words if word and word in text)
            if hits:
                scores[cap] = hits

        if scores:
            best_cap = max(scores.items(), key=lambda kv: (kv[1], kv[0].value))[0]
            return {
                "capability": best_cap,
                "source": "keyword",
                "scores": {c.value: n for c, n in scores.items()},
                "matched_pattern": "",
                "reason": (
                    f"prompt 关键词打分最高者为 {best_cap.value} "
                    f"({scores[best_cap]} 次命中)"
                ),
            }

        # 3. Declared domain
        if domain_key and domain_key in self._domain_map:
            resolved = self._domain_map[domain_key]
            return {
                "capability": resolved,
                "source": "domain",
                "scores": {},
                "matched_pattern": "",
                "reason": (
                    f"prompt 无有效信号，按声明领域 {domain_key!r} "
                    f"映射为 {resolved.value}"
                ),
            }

        # 4. Give up honestly
        return {
            "capability": Capability.UNKNOWN,
            "source": "default",
            "scores": {},
            "matched_pattern": "",
            "reason": (
                "prompt 与 domain 均无可用信号，返回 UNKNOWN（不猜测）"
            ),
        }

    def classify_domain(self, domain: str) -> Capability:
        """Map a bare domain string to a capability (no prompt evidence).

        Used by minister selection to bucket each minister's declared
        genome domain into the same capability space as the incoming task.
        """
        domain_key = "" if domain is None else str(domain).strip().lower()
        if domain_key in self._domain_map:
            return self._domain_map[domain_key]
        return Capability.UNKNOWN

    # ── Tier & fallback ───────────────────────────────────────────

    def get_tier_for_capability(self, cap: Any) -> str:
        """Return the model tier name (``"premium"`` …) for *cap*."""
        capability = Capability.coerce(cap)
        tier = self._tier_map.get(capability, ModelTier.STANDARD)
        return tier.value

    def get_fallback_chain_for_tier(self, tier: Any) -> List[str]:
        """Return an ordered copy of the fallback model chain for *tier*.

        Unknown tiers fall back to the STANDARD chain rather than an empty
        list, so a typo in config can never produce "no models available".
        """
        resolved = ModelTier.coerce(tier)
        chain = self._fallback_chains.get(resolved)
        if not chain:
            chain = self._fallback_chains.get(ModelTier.STANDARD, [])
        return list(chain)

    def route(self, prompt: str, domain: str = "general") -> Dict[str, Any]:
        """One-shot convenience: classify → tier → fallback chain."""
        cap = self.classify(prompt, domain)
        tier = self.get_tier_for_capability(cap)
        chain = self.get_fallback_chain_for_tier(tier)
        return {
            "capability": cap,
            "tier": tier,
            "fallback_chain": chain,
            # First entry is the primary pick; the rest are ordered fallbacks.
            # The chain is guaranteed non-empty by get_fallback_chain_for_tier.
            "model": chain[0] if chain else "",
        }

    # ── Introspection ─────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        """Return a serialisable snapshot of the active routing tables."""
        return {
            "config_path": self.config_path,
            "loaded_from_config": self.loaded_from_config,
            "capabilities": [c.value for c in Capability],
            "domains": {d: c.value for d, c in self._domain_map.items()},
            "tiers": {c.value: t.value for c, t in self._tier_map.items()},
            "fallback_chains": {
                t.value: list(models) for t, models in self._fallback_chains.items()
            },
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SmartRouter(domains={len(self._domain_map)}, "
            f"tiers={len(self._tier_map)}, "
            f"config={self.config_path!r})"
        )


__all__ = [
    "Capability",
    "ModelTier",
    "SmartRouter",
    "DEFAULT_DOMAIN_MAP",
    "DEFAULT_KEYWORD_MAP",
    "DEFAULT_TIER_MAP",
    "DEFAULT_FALLBACK_CHAINS",
]
