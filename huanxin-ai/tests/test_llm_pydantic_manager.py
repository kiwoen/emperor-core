"""Tests for huanxin.llm multi-backend manager (pydantic stack, emperor entry).

The pydantic ``LLMEngine``/``LLMConfig``/``ModelProvider`` API is pinned by
``tests/test_llm.py`` and must stay intact. These tests cover the NET-NEW
multi-backend ``LLMManager`` in ``huanxin.llm`` and verify it delegates correctly
to ``huanxin.core.llm.LLMManager`` (shared failover / free-provider registry), so
the emperor entry behaves identically to the domains main chain.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock

import pytest

import huanxin.core.llm as core_llm
import huanxin.llm as llm
from huanxin.llm import LLMConfig, LLMManager, ModelProvider, build_manager_from_env


# ── Helpers ──────────────────────────────────────────────────────────

class _Msg:
    content = "live-answer"

class _Choice:
    message = _Msg()

class _Resp:
    choices = [_Choice()]


@pytest.fixture
def fake_litellm(monkeypatch):
    """Replace core's litellm global with a controllable AsyncMock."""
    mock = types.SimpleNamespace()
    mock.acompletion = None  # set per-test
    monkeypatch.setattr(core_llm, "litellm", mock)
    return mock


def _neutralize_routers(mgr: LLMManager) -> None:
    """Disable the optional ModelRouter so tests are deterministic."""
    for eng in mgr._core.engines:
        eng.router = None


# ── LLMConfig adapters ──────────────────────────────────────────────

class TestLLMConfigAdapters:
    def test_from_env_defaults(self, monkeypatch):
        for v in ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_PROVIDER",
                  "OPENAI_TEMPERATURE", "OPENAI_MAX_TOKENS"]:
            monkeypatch.delenv(v, raising=False)
        cfg = LLMConfig.from_env()
        assert cfg.provider == ModelProvider.OPENAI
        assert cfg.model_name == "gpt-4o"
        assert cfg.api_key == ""
        assert cfg.base_url == ""
        assert cfg.temperature == 0.7
        assert cfg.max_tokens == 4096

    def test_from_env_reads_vars(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-abc")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://x.example/v1")
        monkeypatch.setenv("OPENAI_MODEL", "deepseek-chat")
        monkeypatch.setenv("OPENAI_PROVIDER", "ollama")
        monkeypatch.setenv("OPENAI_TEMPERATURE", "0.2")
        monkeypatch.setenv("OPENAI_MAX_TOKENS", "2048")
        cfg = LLMConfig.from_env()
        assert cfg.provider == ModelProvider.OLLAMA
        assert cfg.model_name == "deepseek-chat"
        assert cfg.api_key == "sk-abc"
        assert cfg.base_url == "https://x.example/v1"
        assert cfg.temperature == 0.2
        assert cfg.max_tokens == 2048

    def test_from_env_unknown_provider_falls_back(self, monkeypatch):
        monkeypatch.setenv("OPENAI_PROVIDER", "not-a-real-provider")
        cfg = LLMConfig.from_env()
        assert cfg.provider == ModelProvider.OPENAI

    def test_to_core_and_back(self):
        cfg = LLMConfig(
            provider=ModelProvider.OPENAI,
            model_name="gpt-4o-mini",
            api_key="sk-x",
            base_url="https://p.example/v1",
            temperature=0.5,
            max_tokens=512,
        )
        core_cfg = cfg.to_core()
        assert core_cfg.model == "gpt-4o-mini"
        assert core_cfg.provider == "openai"
        assert core_cfg.api_key == "sk-x"
        assert core_cfg.base_url == "https://p.example/v1"
        assert core_cfg.temperature == 0.5
        assert core_cfg.max_tokens == 512
        # round-trip
        back = LLMConfig.from_core(core_cfg)
        assert back.model_name == cfg.model_name
        assert back.provider == cfg.provider
        assert back.api_key == cfg.api_key
        assert back.base_url == cfg.base_url


# ── LLMManager (delegating to core) ─────────────────────────────────

class TestLLMManager:
    def test_mock_fallback_single_backend(self):
        """No key/url => mock_mode, returns mock text, config is pydantic."""
        mgr = LLMManager(backends=[LLMConfig(provider=ModelProvider.OPENAI, model_name="gpt-4o")])
        assert mgr.mock_mode is True
        assert isinstance(mgr.config, LLMConfig)
        assert mgr.config.model_name == "gpt-4o"
        reply = mgr.chat_sync("hello")
        assert isinstance(reply, str) and reply  # mock template

    def test_live_single_backend(self, fake_litellm):
        fake_litellm.acompletion = AsyncMock(return_value=_Resp())
        mgr = LLMManager(backends=[LLMConfig(
            provider=ModelProvider.OPENAI, model_name="gpt-4o", api_key="sk-x",
            base_url="https://p.example/v1",
        )])
        _neutralize_routers(mgr)
        assert mgr.mock_mode is False
        reply = mgr.chat_sync("hi")
        assert reply == "live-answer"

    def test_failover_to_second_backend(self, fake_litellm):
        call_log = []

        async def side(**kw):
            call_log.append(kw["model"])
            if "bad" in kw["model"]:
                raise RuntimeError("backend down")
            return _Resp()

        fake_litellm.acompletion = side
        mgr = LLMManager(backends=[
            LLMConfig(provider=ModelProvider.OPENAI, model_name="bad-model", api_key="sk-1", base_url="https://a/v1"),
            LLMConfig(provider=ModelProvider.OPENAI, model_name="good-model", api_key="sk-2", base_url="https://b/v1"),
        ])
        _neutralize_routers(mgr)
        reply = mgr.chat_sync("hi")
        assert reply == "live-answer"
        assert mgr.last_used_backend == 1
        assert len(call_log) == 2  # tried bad, then good

    def test_config_stays_pydantic_after_failover(self, fake_litellm):
        fake_litellm.acompletion = AsyncMock(return_value=_Resp())
        mgr = LLMManager(backends=[
            LLMConfig(provider=ModelProvider.OPENAI, model_name="first", api_key="sk-1", base_url="https://a/v1"),
            LLMConfig(provider=ModelProvider.OLLAMA, model_name="llama3", api_key="", base_url="http://localhost:11434/v1"),
        ])
        _neutralize_routers(mgr)
        assert isinstance(mgr.config, LLMConfig)
        assert mgr.config.model_name == "first"
        assert mgr.config.provider == ModelProvider.OPENAI

    def test_cost_report_delegates(self, fake_litellm):
        fake_litellm.acompletion = AsyncMock(return_value=_Resp())
        mgr = LLMManager(backends=[LLMConfig(
            provider=ModelProvider.OPENAI, model_name="gpt-4o", api_key="sk-x", base_url="https://p/v1",
        )])
        _neutralize_routers(mgr)
        rep = mgr.get_cost_report()
        assert isinstance(rep, dict)
        assert rep["backends"] == 1

    def test_async_chat_works(self, fake_litellm):
        fake_litellm.acompletion = AsyncMock(return_value=_Resp())
        mgr = LLMManager(backends=[LLMConfig(
            provider=ModelProvider.OPENAI, model_name="gpt-4o", api_key="sk-x", base_url="https://p/v1",
        )])
        _neutralize_routers(mgr)
        reply = asyncio.run(mgr.chat("hi"))
        assert reply == "live-answer"


# ── build_manager_from_env ───────────────────────────────────────────

class TestBuildManagerFromEnv:
    def test_single_backend_from_env(self, monkeypatch):
        for v in ["OPENAI_BASE_URL", "OPENAI_FALLBACK_PROVIDERS", "OPENAI_FALLBACK_BASE_URLS"]:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        mgr = build_manager_from_env()
        assert isinstance(mgr, LLMManager)
        assert len(mgr.backends) == 1
        assert mgr.config.model_name == "gpt-4o"

    def test_free_provider_fallback_appended(self, monkeypatch):
        for v in ["OPENAI_BASE_URL", "OPENAI_FALLBACK_BASE_URLS"]:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("OPENAI_FALLBACK_PROVIDERS", "ollama")
        mgr = build_manager_from_env()
        assert isinstance(mgr, LLMManager)
        # primary + ollama registry entry
        assert len(mgr.backends) >= 2
        assert mgr.config.model_name == "gpt-4o"
        assert any(b.base_url == "http://localhost:11434/v1" for b in mgr.backends)

    def test_no_env_uses_mock_primary(self, monkeypatch):
        for v in ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_FALLBACK_PROVIDERS",
                  "OPENAI_FALLBACK_BASE_URLS", "OPENAI_MODEL"]:
            monkeypatch.delenv(v, raising=False)
        mgr = build_manager_from_env()
        assert isinstance(mgr, LLMManager)
        assert mgr.mock_mode is True
