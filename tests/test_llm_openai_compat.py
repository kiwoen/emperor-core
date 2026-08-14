"""Tests for OpenAI-compatible (free model) LLM wiring in jarvis.core.llm.

These prove the plumbing without network access:
- OPENAI_* env vars drive the config
- a Base URL alone (no key) switches to LIVE mode (keyless proxies)
- litellm is called with the correct api_base / model / dummy key
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import jarvis.core.llm as llm_mod
from jarvis.core.llm import LLMConfig, LLMEngine


@pytest.fixture
def fake_litellm(monkeypatch):
    mock = MagicMock()
    mock.acompletion = AsyncMock()
    mock.acompletion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="pong"))]
    )
    monkeypatch.setattr(llm_mod, "litellm", mock)
    return mock


def test_from_env_reads_openai_vars(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-abc")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    cfg = LLMConfig.from_env()
    assert cfg.base_url == "https://example.com/v1"
    assert cfg.api_key == "sk-abc"
    assert cfg.model == "gpt-4o-mini"
    # explicit overrides win over env
    cfg2 = LLMConfig.from_env(model="claude-x")
    assert cfg2.model == "claude-x"


def test_base_url_only_enables_live_mode(fake_litellm):
    cfg = LLMConfig(base_url="https://example.com/v1", router_enabled=False)
    eng = LLMEngine(cfg)
    assert eng.mock_mode is False


def test_no_creds_stays_mock():
    eng = LLMEngine(LLMConfig(router_enabled=False))
    assert eng.mock_mode is True


def test_explicit_mock_mode_forced():
    eng = LLMEngine(LLMConfig(base_url="https://x/v1", mock_mode=True, router_enabled=False))
    assert eng.mock_mode is True


def test_live_call_passes_api_base_and_dummy_key(fake_litellm):
    cfg = LLMConfig(base_url="https://example.com/v1", model="gpt-4o",
                    api_key="", router_enabled=False)
    eng = LLMEngine(cfg)
    reply = asyncio.run(eng.complete("ping"))
    assert reply == "pong"
    _, kwargs = fake_litellm.acompletion.call_args
    assert kwargs["api_base"] == "https://example.com/v1"
    assert kwargs["api_key"] == "sk-noauth"
    assert kwargs["model"] == "openai/gpt-4o"


def test_live_call_passes_real_key(fake_litellm):
    cfg = LLMConfig(base_url="https://example.com/v1", model="gpt-4o",
                    api_key="sk-real", router_enabled=False)
    eng = LLMEngine(cfg)
    asyncio.run(eng.complete("ping"))
    _, kwargs = fake_litellm.acompletion.call_args
    assert kwargs["api_key"] == "sk-real"


def test_get_llm_env_seeds_live(monkeypatch, fake_litellm):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    llm_mod._llm_instance = None  # reset singleton
    eng = llm_mod.get_llm()
    assert eng.mock_mode is False


def test_init_llm_explicit_config_overrides_env(monkeypatch, fake_litellm):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")

    class _Sub:
        pass

    class _Cfg:
        pass

    _cfg = _Cfg()
    _cfg.llm = _Sub()
    _cfg.llm.base_url = "https://explicit.example/v1"
    _cfg.llm.model = "explicit-model"
    _cfg.llm.api_key = ""
    _cfg.llm.provider = "openai"
    _cfg.llm.temperature = 0.7
    _cfg.llm.max_tokens = 1024

    eng = llm_mod.init_llm(_cfg)
    assert eng.config.base_url == "https://explicit.example/v1"
    assert eng.config.model == "explicit-model"
