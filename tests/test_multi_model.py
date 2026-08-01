"""
tests/test_multi_model.py — 14 tests for MultiModelRouter

Coverage:
- Model registration / lookup / tier filtering
- Parallel invocation (invoke_parallel / invoke_ensemble)
- Strategy routing (cheapest / fastest / best / consensus)
- API endpoints (GET /api/models, POST /api/models/benchmark)
- Edge cases (empty registry, unknown model, unknown strategy)
"""

import json

import pytest
from starlette.testclient import TestClient

from jarvis.multi_model import (
    ModelConfig,
    MultiModelRouter,
    ParallelResult,
)


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def router():
    """Fresh MultiModelRouter with default registry."""
    return MultiModelRouter()


@pytest.fixture
def empty_router():
    """Router with no models."""
    return MultiModelRouter(model_registry={})


@pytest.fixture
def client():
    """Test client for court_api."""
    from jarvis.court_api import create_app
    from jarvis.emperor import Emperor

    emp = Emperor()
    app = create_app()
    app.extra["emperor"] = emp
    app.extra["multi_model_router"] = emp.multi_model_router
    return TestClient(app)


# ──────────────────────────────────────────────
# Test: Model registry & lookup
# ──────────────────────────────────────────────

class TestModelRegistry:
    """Model registration, lookup, and tier filtering."""

    def test_default_models_registered(self, router):
        """All default models should be registered."""
        models = router.list_models()
        model_ids = {m.model_id for m in models}
        assert "deepseek-chat" in model_ids
        assert "deepseek-reasoner" in model_ids
        assert "gpt-4o" in model_ids
        assert "claude-opus" in model_ids
        assert len(models) >= 6

    def test_get_model_by_id(self, router):
        """Get a specific model by ID."""
        m = router.get_model("deepseek-chat")
        assert m is not None
        assert m.model_id == "deepseek-chat"
        assert m.tier == "cheap"
        assert m.provider == "deepseek"
        assert m.supports_parallel_tool_calls is True
        assert m.cost_per_1k_input == 0.00027

    def test_get_unknown_model(self, router):
        """Unknown model returns None."""
        assert router.get_model("nonexistent-model") is None

    def test_list_models_by_tier(self, router):
        """Filter models by tier."""
        cheap = router.list_models(tier="cheap")
        assert all(m.tier == "cheap" for m in cheap)
        assert len(cheap) >= 2  # deepseek-chat + gpt-4o-mini

        premium = router.list_models(tier="premium")
        assert all(m.tier == "premium" for m in premium)
        assert len(premium) >= 1

        # Non-existent tier
        assert router.list_models(tier="nonexistent") == []

    def test_register_custom_model(self, router):
        """Register a new model dynamically."""
        custom = ModelConfig(
            model_id="my-custom-model",
            tier="standard",
            display_name="Custom Model",
            provider="custom",
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.005,
            supports_parallel_tool_calls=True,
        )
        router.register_model(custom)
        m = router.get_model("my-custom-model")
        assert m is not None
        assert m.tier == "standard"
        assert m.cost_per_1k_input == 0.001

    def test_get_all_tiers(self, router):
        """get_all_tiers returns all distinct tiers."""
        tiers = router.get_all_tiers()
        assert "cheap" in tiers
        assert "standard" in tiers
        assert "premium" in tiers


# ──────────────────────────────────────────────
# Test: Strategy routing
# ──────────────────────────────────────────────

class TestStrategyRouting:
    """Strategy-based model selection."""

    def test_route_cheapest(self, router):
        """Cheapest strategy selects the lowest-cost model."""
        result = router.route(strategy="cheapest")
        assert result.success is True
        assert result.model_id == "gpt-4o-mini"  # cheapest at $0.00015/1k input

    def test_route_fastest(self, router):
        """Fastest strategy selects the lowest-latency model."""
        result = router.route(strategy="fastest")
        assert result.success is True
        # Claude Haiku has the lowest estimate (500ms)
        assert result.model_id == "claude-haiku"

    def test_route_best(self, router):
        """Best strategy selects highest-tier model."""
        result = router.route(strategy="best")
        assert result.success is True
        assert result.tier == "premium"
        assert result.model_id == "claude-opus"

    def test_route_unknown_strategy(self, router):
        """Unknown strategy returns failure."""
        result = router.route(strategy="quantum-leap")
        assert result.success is False
        assert "Unknown strategy" in result.error

    def test_route_by_cost_budget_fail(self, router):
        """If budget is too low even for cheapest model, route fails."""
        result = router.route(strategy="cheapest", budget_usd=0.0000001)
        assert result.success is False
        assert "insufficient" in result.error.lower()

    def test_route_consensus(self, router):
        """Consensus strategy returns models from different providers."""
        models = router.route_consensus(n_models=3)
        assert len(models) == 3
        providers = {m.provider for m in models}
        # Should have at least 2 different providers
        assert len(providers) >= 2


# ──────────────────────────────────────────────
# Test: Parallel invocation
# ──────────────────────────────────────────────

class TestParallelInvocation:
    """Parallel and ensemble model calls."""

    def test_invoke_parallel(self, router):
        """invoke_parallel calls same model n times."""
        messages = [{"role": "user", "content": "What is 2+2?"}]
        results = router.invoke_parallel(messages, model_id="gpt-4o-mini", n=3)
        assert len(results) == 3
        for r in results:
            assert r.success is True
            assert r.model_id == "gpt-4o-mini"
            assert r.latency_ms > 0
            assert "GPT-4o Mini" in r.output

    def test_invoke_ensemble(self, router):
        """invoke_ensemble calls multiple different models."""
        messages = [{"role": "user", "content": "Explain TCP in one sentence."}]
        model_ids = ["gpt-4o-mini", "deepseek-chat", "claude-sonnet"]
        results = router.invoke_ensemble(messages, model_ids=model_ids)

        assert len(results) == 3
        result_models = {r.model_id for r in results}
        assert result_models == set(model_ids)

        for r in results:
            assert r.success is True
            assert r.latency_ms > 0
            assert len(r.output) > 0

    def test_ensemble_defaults_one_per_tier(self, router):
        """invoke_ensemble without model_ids uses one per tier."""
        messages = [{"role": "user", "content": "Hello"}]
        results = router.invoke_ensemble(messages)
        tiers = {r.tier for r in results if r.success}
        assert len(tiers) >= 2
        assert len(results) >= 2

    def test_benchmark_sorted_by_latency(self, router):
        """benchmark returns results sorted by latency ascending."""
        messages = [{"role": "user", "content": "Benchmark test"}]
        results = router.benchmark(messages, model_ids=["gpt-4o-mini", "claude-opus"])

        assert len(results) == 2
        # claude-opus is slowest → should be last
        assert results[0].latency_ms <= results[1].latency_ms


# ──────────────────────────────────────────────
# Test: Edge cases
# ──────────────────────────────────────────────

class TestEdgeCases:
    """Edge case handling."""

    def test_empty_registry_route(self, empty_router):
        """Routing with no models returns failure."""
        result = empty_router.route(strategy="cheapest")
        assert result.success is False
        assert "No models" in result.error

    def test_empty_registry_parallel(self, empty_router):
        """Parallel call with no models returns failure."""
        result = empty_router.invoke_parallel(
            [{"role": "user", "content": "test"}],
            n=1,
        )
        assert len(result) == 1
        assert result[0].success is False

    def test_reset_clears_counters(self, router):
        """reset() clears all counters and caches."""
        router.invoke_parallel(
            [{"role": "user", "content": "test"}],
            model_id="gpt-4o-mini",
            n=2,
        )
        assert router.total_calls > 0
        assert router.calls_by_model
        assert router._latency_cache

        router.reset()
        assert router.total_calls == 0
        assert router.calls_by_model == {}
        assert router._latency_cache == {}

    def test_stats(self, router):
        """stats() returns usage info."""
        router.invoke_parallel(
            [{"role": "user", "content": "test"}],
            model_id="gpt-4o-mini",
            n=1,
        )
        s = router.stats()
        assert s["total_calls"] == 1
        assert "gpt-4o-mini" in s["calls_by_model"]
        assert s["registered_models"] >= 6


# ──────────────────────────────────────────────
# Test: API endpoints
# ──────────────────────────────────────────────

class TestAPIEndpoints:
    """Integration tests for /api/models and /api/models/benchmark."""

    def test_get_models_list(self, client):
        """GET /api/models returns all models."""
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 6
        assert "models" in data
        assert "tiers" in data
        # Spot-check a model
        model_ids = [m["model_id"] for m in data["models"]]
        assert "deepseek-chat" in model_ids
        assert "deepseek-reasoner" in model_ids

    def test_get_models_by_tier(self, client):
        """GET /api/models?tier=cheap returns only cheap-tier models."""
        resp = client.get("/api/models?tier=cheap")
        assert resp.status_code == 200
        data = resp.json()
        for m in data["models"]:
            assert m["tier"] == "cheap"

    def test_benchmark_endpoint(self, client):
        """POST /api/models/benchmark returns results for all models."""
        resp = client.post(
            "/api/models/benchmark",
            json={"prompt": "What is the speed of light?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["prompt"] == "What is the speed of light?"
        assert "results" in data
        assert len(data["results"]) >= 6
        assert "fastest" in data

        # Each result has required fields
        for r in data["results"]:
            assert "model_id" in r
            assert "tier" in r
            assert "latency_ms" in r
            assert "success" in r
            assert r["success"] is True

    def test_benchmark_missing_prompt(self, client):
        """POST /api/models/benchmark without prompt returns 400."""
        resp = client.post("/api/models/benchmark", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert "prompt is required" in data["detail"].lower()

    def test_benchmark_specific_models(self, client):
        """POST /api/models/benchmark with specific model_ids."""
        resp = client.post(
            "/api/models/benchmark",
            json={
                "prompt": "Hello",
                "model_ids": ["deepseek-chat", "deepseek-reasoner"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 2
        model_ids = [r["model_id"] for r in data["results"]]
        assert set(model_ids) == {"deepseek-chat", "deepseek-reasoner"}


# ──────────────────────────────────────────────
# Test: ModelConfig serialization
# ──────────────────────────────────────────────

class TestModelConfig:
    """ModelConfig dataclass tests."""

    def test_to_dict(self):
        """to_dict() returns all expected fields."""
        cfg = ModelConfig(
            model_id="test-model",
            tier="cheap",
            display_name="Test Model",
            provider="test",
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.005,
            max_tokens=4096,
            context_window=65536,
            supports_parallel_tool_calls=True,
            supports_reasoning=True,
            latency_ms_estimate=100.0,
        )
        d = cfg.to_dict()
        assert d["model_id"] == "test-model"
        assert d["tier"] == "cheap"
        assert d["supports_parallel_tool_calls"] is True
        assert d["supports_reasoning"] is True
        assert d["latency_ms_estimate"] == 100.0
