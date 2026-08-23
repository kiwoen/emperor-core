"""
Tests for the Imperial Court Model Provider layer.

Covers:
    - ProviderRegistry: building, availability detection, fallback logic
    - Minister provider injection: real-model try / mock fallback
    - Provider status reporting
    - Integration with Emperor (install_ministers_from_factory)
"""

from __future__ import annotations

import os
import pytest

from jarvis.court.minister import Edict, Minister, MinisterProfile, MinisterState
from jarvis.court.emperor import Emperor, ImperialCourt
from jarvis.court.providers.base import GenerationParams, ModelProvider, ModelResponse
from jarvis.court.providers.openai_provider import OpenAIProvider
from jarvis.court.providers.anthropic_provider import AnthropicProvider
from jarvis.court.providers.google_provider import GoogleProvider
from jarvis.court.providers.registry import (
    ProviderConfig,
    ProviderRegistry,
    MINISTER_PROVIDER_CONFIG,
    get_provider_registry,
    reset_provider_registry,
)


# ── Helpers ─────────────────────────────────────────────────────────


class FakeProvider(ModelProvider):
    """A fake provider that returns a canned response for testing."""

    def __init__(self, model="fake-model", fail=False):
        super().__init__(model=model, api_key="fake-key")
        self.fail = fail
        self.call_count = 0
        self.last_prompt = ""

    @property
    def is_available(self):
        return not self.fail

    async def _generate(self, prompt, params):
        self.call_count += 1
        self.last_prompt = prompt
        if self.fail:
            raise RuntimeError("FakeProvider failure")
        return ModelResponse(
            text=f"[Fake:{self.model}] {prompt[:40]}...",
            model=self.model,
            confidence=0.92,
        )


class FakeMinister(Minister):
    """A test minister with a simple _handle."""

    def __init__(self, name="测试大臣"):
        profile = MinisterProfile(
            title=name,
            archetype="Test-AI",
            domain="testing",
            strengths=["testing", "验证"],
            weaknesses=["nothing"],
            quality_score=0.80,
        )
        system_prompt = (
            "你是{title}，测试大臣。"
            "擅长：{strengths}。"
        )
        super().__init__(profile, system_prompt_template=system_prompt)

    async def _handle(self, edict):
        return f"[Mock:{self.name}]{edict.intent}", 0.75


# ── Provider Registry Tests ─────────────────────────────────────────


class TestProviderRegistry:
    """Tests for ProviderRegistry creation and availability detection."""

    def setup_method(self):
        reset_provider_registry()
        # Clear API keys for clean test state
        for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY"]:
            if key in os.environ:
                del os.environ[key]

    def test_registry_builds_without_keys(self):
        """Registry should build successfully even with zero API keys."""
        registry = get_provider_registry()
        assert registry._built
        # All ministers should have None providers (mock mode)
        for name in MINISTER_PROVIDER_CONFIG:
            assert registry.get_provider(name) is None

    def test_registry_returns_none_when_no_keys(self):
        """get_provider returns None when no keys are configured."""
        registry = get_provider_registry()
        assert registry.get_provider("丞相") is None
        assert registry.get_provider("御史大夫") is None

    def test_registry_uses_openai_key(self):
        """When OPENAI_API_KEY is set, OpenAI-dependent ministers get providers."""
        os.environ["OPENAI_API_KEY"] = "sk-test123"
        reset_provider_registry()

        registry = get_provider_registry()
        chancellor = registry.get_provider("丞相")
        assert chancellor is not None
        assert isinstance(chancellor, OpenAIProvider)
        assert chancellor.model == "gpt-5"

    def test_registry_uses_deepseek_key(self):
        """When DEEPSEEK_API_KEY is set, DeepSeek ministers get providers."""
        os.environ["DEEPSEEK_API_KEY"] = "ds-test456"
        reset_provider_registry()

        registry = get_provider_registry()
        works = registry.get_provider("工部尚书")
        assert works is not None
        assert isinstance(works, OpenAIProvider)
        assert works.model == "deepseek-chat"
        assert works.base_url == "https://api.deepseek.com/v1"

    def test_registry_fallback_to_openai_for_google(self):
        """When GOOGLE_API_KEY missing but OPENAI_API_KEY present, use fallback."""
        os.environ["OPENAI_API_KEY"] = "sk-fallback"
        reset_provider_registry()

        registry = get_provider_registry()
        ceremonies = registry.get_provider("太常")
        assert ceremonies is not None
        assert isinstance(ceremonies, OpenAIProvider)
        assert ceremonies.model == "gpt-4o"  # fallback

    def test_registry_status(self):
        """get_status returns correct availability for all ministers."""
        os.environ["OPENAI_API_KEY"] = "sk-status"
        reset_provider_registry()

        registry = get_provider_registry()
        status = registry.get_status()

        assert "丞相" in status
        assert status["丞相"]["available"] is True
        assert status["御史大夫"]["available"] is False  # no ANTHROPIC key

    def test_registry_singleton(self):
        """get_provider_registry returns the same instance."""
        r1 = get_provider_registry()
        r2 = get_provider_registry()
        assert r1 is r2

    def test_registry_reset(self):
        """reset_provider_registry creates a new singleton."""
        os.environ["OPENAI_API_KEY"] = "sk-reset"
        r1 = get_provider_registry()
        assert r1.get_provider("丞相") is not None

        del os.environ["OPENAI_API_KEY"]
        reset_provider_registry()
        r2 = get_provider_registry()
        assert r2.get_provider("丞相") is None


# ── Minister Provider Injection Tests ───────────────────────────────


class TestMinisterProviderInjection:
    """Tests for minister behavior with real providers vs mock fallback."""

    def test_minister_has_no_provider_by_default(self):
        """New ministers start without a provider (mock mode)."""
        minister = FakeMinister()
        assert not minister.has_real_model
        assert minister._provider is None

    def test_minister_set_provider(self):
        """set_provider injects a real model."""
        minister = FakeMinister()
        fake = FakeProvider()
        minister.set_provider(fake)
        assert minister.has_real_model

    def test_minister_uses_real_model_when_available(self):
        """When a real provider is injected, _try_real_model returns its result."""
        minister = FakeMinister("测试")
        fake = FakeProvider(model="test-gpt")
        minister.set_provider(fake)

        edict = Edict(edict_id="e1", intent="帮我分析代码")
        # receive_edict will try real model first
        import asyncio
        memorial = asyncio.run(minister.receive_edict(edict))

        assert memorial.success
        assert "[Fake:test-gpt]" in memorial.output
        assert memorial.confidence == 0.92
        assert fake.call_count == 1

    def test_minister_falls_back_to_mock_when_no_provider(self):
        """Without a real provider, minister uses mock _handle()."""
        minister = FakeMinister("mock-only")
        edict = Edict(edict_id="e2", intent="测试意图")

        import asyncio
        memorial = asyncio.run(minister.receive_edict(edict))

        assert memorial.success
        assert "[Mock:mock-only]" in memorial.output
        assert memorial.confidence == 0.75

    def test_minister_falls_back_on_provider_error(self):
        """When the real provider raises, fall back to mock."""
        minister = FakeMinister("fallback-test")
        fake = FakeProvider(fail=True)  # will raise RuntimeError
        fake.api_key = "fake"  # so is_available is True
        minister.set_provider(fake)

        edict = Edict(edict_id="e3", intent="另一个测试")
        import asyncio
        memorial = asyncio.run(minister.receive_edict(edict))

        # Should fall back to mock
        assert memorial.success
        assert "[Mock:fallback-test]" in memorial.output

    def test_minister_system_prompt_building(self):
        """_build_system_prompt fills in minister metadata."""
        minister = FakeMinister("prompt-test")
        prompt = minister._build_system_prompt()
        assert "prompt-test" in prompt
        assert "testing" in prompt

    def test_minister_without_template_returns_empty(self):
        """Minister without system_prompt_template returns empty string."""
        profile = MinisterProfile(
            title="无模板",
            archetype="No-Template-AI",
            domain="empty",
            strengths=["none"],
            weaknesses=[],
        )

        class NoTemplateMinister(Minister):
            async def _handle(self, edict):
                return "ok", 0.5

        m = NoTemplateMinister(profile)
        assert m._build_system_prompt() == ""


# ── Emperor + Provider Integration Tests ────────────────────────────


class TestEmperorProviderIntegration:
    """Tests that Emperor correctly injects providers into ministers."""

    def setup_method(self):
        reset_provider_registry()
        for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY"]:
            if key in os.environ:
                del os.environ[key]

    def test_emperor_installs_ministers_without_keys(self):
        """Even without API keys, emperor installs all 8 ministers (mock mode)."""
        court = ImperialCourt()
        court.install_ministers_from_factory()

        assert len(court.ministers) == 8
        # All should be in mock mode
        for name, minister in court.ministers.items():
            assert not minister.has_real_model

    def test_emperor_installs_with_openai_key(self):
        """With OPENAI_API_KEY, relevant ministers get real providers."""
        os.environ["OPENAI_API_KEY"] = "sk-emperor-test"
        reset_provider_registry()

        court = ImperialCourt()
        court.install_ministers_from_factory()

        # Chancellor should have a real model
        assert court.ministers["丞相"].has_real_model
        # Censor should still be mock (needs Anthropic key)
        assert not court.ministers["御史大夫"].has_real_model

    def test_emperor_full_pipeline_mock_mode(self):
        """Full receive_petition works in mock mode."""
        import asyncio

        async def run():
            emperor = Emperor()
            decree = await emperor.receive_petition("分析系统性能瓶颈")
            return decree

        decree = asyncio.run(run())
        assert decree.success
        assert len(decree.ministers_consulted) >= 1
        assert decree.confidence > 0

    def test_court_metrics_include_provider_info(self):
        """Court metrics works with provider integration."""
        court = ImperialCourt()
        court.install_ministers_from_factory()

        metrics = court.get_court_metrics()
        assert metrics["minister_count"] == 8
        assert metrics["decree_count"] == 0


# ── Provider Base Class Tests ───────────────────────────────────────


class TestModelProviders:
    """Tests for the abstract and concrete provider implementations."""

    def test_generation_params_defaults(self):
        """GenerationParams has sensible defaults."""
        params = GenerationParams()
        assert params.temperature == 0.7
        assert params.max_tokens == 2048
        assert params.system_prompt == ""

    def test_model_response_creation(self):
        """ModelResponse dataclass works."""
        resp = ModelResponse(
            text="Hello", model="test", tokens_used=10, confidence=0.9,
        )
        assert resp.text == "Hello"
        assert resp.confidence == 0.9

    def test_openai_provider_not_available_without_key(self):
        """OpenAIProvider without a key is not available."""
        provider = OpenAIProvider(model="gpt-4o")
        assert not provider.is_available

    def test_openai_provider_available_with_key(self):
        """OpenAIProvider with a key is available."""
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        assert provider.is_available

    def test_anthropic_provider_not_available_without_key(self):
        """AnthropicProvider without a key is not available."""
        provider = AnthropicProvider(model="claude-sonnet-4-20250514")
        assert not provider.is_available

    def test_google_provider_not_available_without_key(self):
        """GoogleProvider without a key is not available."""
        provider = GoogleProvider(model="gemini-2.5-pro")
        assert not provider.is_available

    def test_provider_generate_returns_none_when_unavailable(self):
        """generate() returns None when no API key is set."""
        import asyncio

        async def run():
            provider = OpenAIProvider(model="gpt-4o")
            result = await provider.generate("Hello")
            return result

        result = asyncio.run(run())
        assert result is None
