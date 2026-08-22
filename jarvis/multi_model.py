"""
JARVIS Multi-Model Router — Enhanced model registry with parallel calls,
strategy routing, and DeepSeek V3/R1 support.

Supports cheapest/fastest/best/consensus strategies, parallel invocation
for self-consistency voting, and ensemble calls across multiple models.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.cost_tracker import CostTracker
    from jarvis.multi_model_executor import LLMExecutor

logger = logging.getLogger("jarvis.multi_model")


# ══════════════════════════════════════════════════════════════════
# Model Registry
# ══════════════════════════════════════════════════════════════════

@dataclass
class ModelConfig:
    """Configuration for a registered model."""
    model_id: str
    tier: str  # cheap | standard | premium
    display_name: str = ""
    provider: str = ""
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    max_tokens: int = 4096
    context_window: int = 128_000
    supports_parallel_tool_calls: bool = False
    supports_reasoning: bool = False
    latency_ms_estimate: float = 0.0  # estimated baseline latency

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "tier": self.tier,
            "display_name": self.display_name,
            "provider": self.provider,
            "cost_per_1k_input": self.cost_per_1k_input,
            "cost_per_1k_output": self.cost_per_1k_output,
            "max_tokens": self.max_tokens,
            "context_window": self.context_window,
            "supports_parallel_tool_calls": self.supports_parallel_tool_calls,
            "supports_reasoning": self.supports_reasoning,
            "latency_ms_estimate": self.latency_ms_estimate,
        }


# ── Default model registry ──────────────────────────────────────────

_DEFAULT_MODELS: dict[str, ModelConfig] = {
    # ── Cheap tier ──
    "gpt-4o-mini": ModelConfig(
        model_id="gpt-4o-mini",
        tier="cheap",
        display_name="GPT-4o Mini",
        provider="openai",
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.00060,
        max_tokens=16384,
        context_window=128_000,
        supports_parallel_tool_calls=True,
        supports_reasoning=False,
        latency_ms_estimate=600.0,
    ),
    "deepseek-chat": ModelConfig(
        model_id="deepseek-chat",
        tier="cheap",
        display_name="DeepSeek V3",
        provider="deepseek",
        cost_per_1k_input=0.00027,
        cost_per_1k_output=0.00110,
        max_tokens=8192,
        context_window=128_000,
        supports_parallel_tool_calls=True,
        supports_reasoning=False,
        latency_ms_estimate=800.0,
    ),
    "claude-haiku": ModelConfig(
        model_id="claude-haiku",
        tier="cheap",
        display_name="Claude 3.5 Haiku",
        provider="anthropic",
        cost_per_1k_input=0.00080,
        cost_per_1k_output=0.00400,
        max_tokens=8192,
        context_window=200_000,
        supports_parallel_tool_calls=True,
        supports_reasoning=False,
        latency_ms_estimate=500.0,
    ),

    # ── Standard tier ──
    "gpt-4o": ModelConfig(
        model_id="gpt-4o",
        tier="standard",
        display_name="GPT-4o",
        provider="openai",
        cost_per_1k_input=0.00250,
        cost_per_1k_output=0.01000,
        max_tokens=16384,
        context_window=128_000,
        supports_parallel_tool_calls=True,
        supports_reasoning=False,
        latency_ms_estimate=1200.0,
    ),
    "deepseek-reasoner": ModelConfig(
        model_id="deepseek-reasoner",
        tier="standard",
        display_name="DeepSeek R1",
        provider="deepseek",
        cost_per_1k_input=0.00055,
        cost_per_1k_output=0.00219,
        max_tokens=8192,
        context_window=128_000,
        supports_parallel_tool_calls=False,
        supports_reasoning=True,
        latency_ms_estimate=3000.0,
    ),
    "claude-sonnet": ModelConfig(
        model_id="claude-sonnet",
        tier="standard",
        display_name="Claude 3.7 Sonnet",
        provider="anthropic",
        cost_per_1k_input=0.00300,
        cost_per_1k_output=0.01500,
        max_tokens=8192,
        context_window=200_000,
        supports_parallel_tool_calls=True,
        supports_reasoning=False,
        latency_ms_estimate=1500.0,
    ),

    # ── Premium tier ──
    "claude-opus": ModelConfig(
        model_id="claude-opus",
        tier="premium",
        display_name="Claude 4 Opus",
        provider="anthropic",
        cost_per_1k_input=0.01500,
        cost_per_1k_output=0.07500,
        max_tokens=16384,
        context_window=200_000,
        supports_parallel_tool_calls=True,
        supports_reasoning=True,
        latency_ms_estimate=3000.0,
    ),
}


# ══════════════════════════════════════════════════════════════════
# Parallel Call Result
# ══════════════════════════════════════════════════════════════════

@dataclass
class ParallelResult:
    """Result from a parallel or ensemble model call."""
    model_id: str
    tier: str
    output: str = ""
    latency_ms: float = 0.0
    success: bool = True
    error: str = ""
    cost_estimate: float = 0.0
    tokens_in: int = 0  # prompt tokens consumed (0 => router estimates)
    tokens_out: int = 0  # completion tokens produced


# ══════════════════════════════════════════════════════════════════
# MultiModelRouter
# ══════════════════════════════════════════════════════════════════

class MultiModelRouter:
    """Enhanced multi-model router with parallel calls and strategy routing.

    Usage::

        router = MultiModelRouter()
        result = router.route("Hello!", strategy="cheapest")
        # → ParallelResult(model_id="gpt-4o-mini", ...)

        results = router.invoke_parallel([...], n=3)
        # → [ParallelResult, ParallelResult, ParallelResult]

        results = router.invoke_ensemble([...], ["gpt-4o", "claude-sonnet"])
        # → [ParallelResult, ...]
    """

    def __init__(
        self,
        model_registry: dict[str, ModelConfig] | None = None,
        cost_tracker: Optional[CostTracker] = None,
        executor: "Optional[LLMExecutor]" = None,
    ) -> None:
        self._models: dict[str, ModelConfig] = (
            dict(model_registry) if model_registry is not None else dict(_DEFAULT_MODELS)
        )
        self._latency_cache: dict[str, float] = {}  # model_id → last_latency_ms
        self.total_calls: int = 0
        self.calls_by_model: dict[str, int] = {}
        # Cost tracking
        if cost_tracker is not None:
            self.cost_tracker: CostTracker = cost_tracker
        else:
            from jarvis.cost_tracker import CostTracker
            self.cost_tracker: CostTracker = CostTracker()
        # ── Executor selection ──
        # Injectable for tests; otherwise auto-select a real backend when one is
        # reachable, else the offline mock executor.
        if executor is not None:
            self._executor: LLMExecutor = executor
        else:
            from jarvis.multi_model_executor import select_default_executor
            self._executor = select_default_executor()
        logger.info(
            "MultiModelRouter initialized — %d models across %d tiers (executor=%s)",
            len(self._models),
            len({m.tier for m in self._models.values()}),
            type(self._executor).__name__,
        )

    # ── Registry access ──────────────────────────────────────────

    def list_models(self, tier: str | None = None) -> list[ModelConfig]:
        """List all registered models, optionally filtered by tier."""
        models = list(self._models.values())
        if tier:
            models = [m for m in models if m.tier == tier]
        return sorted(models, key=lambda m: m.cost_per_1k_input)

    def get_model(self, model_id: str) -> Optional[ModelConfig]:
        """Get a model config by ID."""
        return self._models.get(model_id)

    def register_model(self, config: ModelConfig) -> None:
        """Register or update a model in the registry."""
        self._models[config.model_id] = config

    def get_tier_models(self, tier: str) -> list[ModelConfig]:
        """Get all models in a given tier."""
        return [m for m in self._models.values() if m.tier == tier]

    def get_all_tiers(self) -> list[str]:
        """Get all distinct tiers."""
        return sorted({m.tier for m in self._models.values()})

    # ── Strategy: cheapest ───────────────────────────────────────

    def route_by_cost(self, budget_usd: float = 0.01) -> ParallelResult:
        """Select the cheapest model that stays within budget."""
        models = self.list_models()
        if not models:
            return ParallelResult(
                model_id="", tier="", success=False,
                error="No models registered",
            )

        # Pick cheapest model (already sorted by cost)
        best = models[0]

        # Estimate if budget is sufficient (assume ~2000 tokens input+output)
        est_total_cost = (best.cost_per_1k_input * 2) + (best.cost_per_1k_output * 2)
        if est_total_cost > budget_usd:
            return ParallelResult(
                model_id=best.model_id,
                tier=best.tier,
                success=False,
                error=f"Budget ${budget_usd:.6f} insufficient for cheapest model "
                      f"(${est_total_cost:.6f} estimated)",
                cost_estimate=est_total_cost,
            )

        return ParallelResult(
            model_id=best.model_id,
            tier=best.tier,
            cost_estimate=est_total_cost,
        )

    # ── Strategy: fastest ────────────────────────────────────────

    def route_by_latency(self) -> ParallelResult:
        """Select the model with the lowest estimated latency."""
        models = self.list_models()
        if not models:
            return ParallelResult(
                model_id="", tier="", success=False,
                error="No models registered",
            )

        # Use cached latency if available, otherwise use estimate
        best = min(
            models,
            key=lambda m: self._latency_cache.get(
                m.model_id, m.latency_ms_estimate or 99999
            ),
        )
        return ParallelResult(
            model_id=best.model_id,
            tier=best.tier,
            latency_ms=self._latency_cache.get(best.model_id, best.latency_ms_estimate),
        )

    # ── Strategy: best ───────────────────────────────────────────

    def route_by_best(self) -> ParallelResult:
        """Select the best model (highest tier, highest capability)."""
        tier_rank = {"premium": 3, "standard": 2, "cheap": 1}
        models = self.list_models()
        if not models:
            return ParallelResult(
                model_id="", tier="", success=False,
                error="No models registered",
            )

        best = max(models, key=lambda m: (
            tier_rank.get(m.tier, 0),
            m.context_window,
            m.supports_parallel_tool_calls,
            m.supports_reasoning,
        ))
        return ParallelResult(
            model_id=best.model_id,
            tier=best.tier,
        )

    # ── Strategy: consensus ──────────────────────────────────────

    def route_consensus(self, n_models: int = 3) -> list[ModelConfig]:
        """Select n models from different providers for consensus voting."""
        all_models = self.list_models()
        seen_providers: set[str] = set()
        selected: list[ModelConfig] = []

        for m in all_models:
            if m.provider not in seen_providers:
                selected.append(m)
                seen_providers.add(m.provider)
            if len(selected) >= n_models:
                break

        # If not enough providers, fill with remaining models
        if len(selected) < n_models:
            for m in all_models:
                if m not in selected:
                    selected.append(m)
                if len(selected) >= n_models:
                    break

        return selected[:n_models]

    # ── Master route ─────────────────────────────────────────────

    def route(
        self,
        strategy: str = "cheapest",
        budget_usd: float = 0.01,
        n_models: int = 3,
    ) -> ParallelResult:
        """Master routing method using the specified strategy.

        Args:
            strategy: One of 'cheapest', 'fastest', 'best', 'consensus'.
            budget_usd: Maximum budget for cheapest strategy.
            n_models: Number of models for consensus strategy.

        Returns:
            ParallelResult with the selected model (or list for consensus).
        """
        strategy = strategy.lower()
        if strategy == "cheapest":
            return self.route_by_cost(budget_usd=budget_usd)
        elif strategy == "fastest":
            return self.route_by_latency()
        elif strategy == "best":
            return self.route_by_best()
        elif strategy == "consensus":
            models = self.route_consensus(n_models=n_models)
            if not models:
                return ParallelResult(
                    model_id="", tier="", success=False,
                    error="No models available for consensus",
                )
            # Return first model as primary, consensus list stored separately
            m = models[0]
            return ParallelResult(
                model_id=m.model_id,
                tier=m.tier,
            )
        else:
            return ParallelResult(
                model_id="", tier="", success=False,
                error=f"Unknown strategy: {strategy}",
            )

    # ── Parallel invocation ──────────────────────────────────────

    def _cached_latency(self, model_id: str) -> float:
        """Last-known latency estimate for a model (or 0 if unknown)."""
        model = self._models.get(model_id)
        if model is None:
            return 0.0
        return self._latency_cache.get(model_id, model.latency_ms_estimate)

    def _execute_one(
        self,
        messages: list[dict],
        model_id: str,
        cached_latency: float,
    ) -> ParallelResult:
        """Run a single model invocation through the configured executor.

        Owns the accounting (latency cache, call counters, cost tracking) so the
        executor stays a pure ``(messages, model, cached_latency) -> result``
        callable. On a genuine "backend unavailable" error from a real executor
        we fall back to the (offline) mock for that one call; any other error is
        returned as a failed result without fabricating output.
        """
        model = self._models.get(model_id)
        if model is None:
            return ParallelResult(
                model_id=model_id,
                tier="",
                success=False,
                error=f"Unknown model: {model_id}",
            )

        try:
            result = self._executor(messages, model, cached_latency)
        except RuntimeError as exc:
            if "backend unavailable" in str(exc):
                logger.warning(
                    "Real executor unavailable (%s); using offline mock fallback.",
                    exc,
                )
                from jarvis.multi_model_executor import OfflineMockExecutor

                result = OfflineMockExecutor()(messages, model, cached_latency)
            else:
                raise

        # ── Account the call (router is the source of truth) ──
        self._latency_cache[model_id] = result.latency_ms
        self.total_calls += 1
        self.calls_by_model[model_id] = self.calls_by_model.get(model_id, 0) + 1

        prompt_text = str(messages[-1].get("content", "")) if messages else ""
        tokens_in = result.tokens_in or max(len(prompt_text) // 4, 1)
        tokens_out = result.tokens_out or 200
        self.cost_tracker.record(
            model_name=model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            task_id="",
            operation="invoke",
        )
        return result

    def invoke_parallel(
        self,
        messages: list[dict],
        model_id: str | None = None,
        n: int = 3,
    ) -> list[ParallelResult]:
        """Invoke the same model n times in parallel (self-consistency).

        Args:
            messages: The prompt messages.
            model_id: Model to use (defaults to cheapest).
            n: Number of parallel calls.

        Returns:
            List of ParallelResult from each call.
        """
        if model_id is None:
            best = self.route_by_cost()
            if not best.success:
                return [best]
            model_id = best.model_id

        if model_id not in self._models:
            return [
                ParallelResult(
                    model_id=model_id,
                    tier="",
                    success=False,
                    error=f"Unknown model: {model_id}",
                )
            ]

        results: list[ParallelResult] = [None] * n  # type: ignore

        with ThreadPoolExecutor(max_workers=min(n, 10)) as executor:
            futures = {
                executor.submit(
                    self._execute_one, messages, model_id, self._cached_latency(model_id)
                ): i
                for i in range(n)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = ParallelResult(
                        model_id=model_id,
                        tier="",
                        success=False,
                        error=str(e),
                    )

        return results

    def invoke_ensemble(
        self,
        messages: list[dict],
        model_ids: list[str] | None = None,
    ) -> list[ParallelResult]:
        """Invoke multiple different models in parallel on the same prompt.

        Args:
            messages: The prompt messages.
            model_ids: List of model IDs to use (defaults to one per tier).

        Returns:
            List of ParallelResult from each model.
        """
        if model_ids is None:
            # Default: one model per tier
            seen_tiers: set[str] = set()
            model_ids = []
            for m in self.list_models():
                if m.tier not in seen_tiers:
                    model_ids.append(m.model_id)
                    seen_tiers.add(m.tier)

        if not model_ids:
            return []

        results: list[ParallelResult] = [None] * len(model_ids)  # type: ignore

        with ThreadPoolExecutor(max_workers=min(max(len(model_ids), 1), 10)) as executor:
            futures = {
                executor.submit(
                    self._execute_one, messages, mid, self._cached_latency(mid)
                ): i
                for i, mid in enumerate(model_ids)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = ParallelResult(
                        model_id=model_ids[idx] if idx < len(model_ids) else "",
                        tier="",
                        success=False,
                        error=str(e),
                    )

        return results

    # ── Benchmark ────────────────────────────────────────────────

    def benchmark(
        self,
        messages: list[dict],
        model_ids: list[str] | None = None,
    ) -> list[ParallelResult]:
        """Benchmark all (or specified) models on a given prompt.

        Args:
            messages: The prompt messages.
            model_ids: Specific models to benchmark (defaults to all).

        Returns:
            List of ParallelResult sorted by latency.
        """
        if model_ids is None:
            model_ids = list(self._models.keys())

        results = self.invoke_ensemble(messages, model_ids)
        # Sort by latency ascending
        results.sort(key=lambda r: r.latency_ms if r.success else float("inf"))
        return results

    # ── Usage statistics ─────────────────────────────────────────

    def stats(self) -> dict:
        """Return usage statistics."""
        return {
            "total_calls": self.total_calls,
            "calls_by_model": dict(self.calls_by_model),
            "registered_models": len(self._models),
            "latency_cache": {
                k: round(v, 2) for k, v in self._latency_cache.items()
            },
        }

    def reset(self) -> None:
        """Reset usage counters and latency cache."""
        self.total_calls = 0
        self.calls_by_model.clear()
        self._latency_cache.clear()
