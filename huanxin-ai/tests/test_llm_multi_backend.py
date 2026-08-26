"""Tests for multi-backend LLM (failover across free OpenAI-compatible endpoints).

Proves the plumbing without network:
- LLMManager fails over to a healthy backend when one errors
- LLMManager prefers a live backend over a mock-only one
- build_manager_from_env assembles primary + preset free providers
- build_manager_from_env returns a single LLMEngine when no fallback configured
- build_default_llm (self-evolution) also fails over across candidates
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import huanxin.core.llm as llm_mod
from huanxin.core.llm import LLMConfig, LLMEngine, LLMManager, build_manager_from_env


@pytest.fixture
def fake_litellm(monkeypatch):
    mock = MagicMock()
    mock.acompletion = AsyncMock()
    mock.acompletion.return_value = _ok_response()
    monkeypatch.setattr(llm_mod, "litellm", mock)
    return mock


def _ok_response(text: str = "pong"):
    return MagicMock(choices=[MagicMock(message=MagicMock(content=text))])


def test_manager_fails_over_on_error(monkeypatch, fake_litellm):
    # primary raises -> manager fails over to secondary which succeeds
    fake_litellm.acompletion.side_effect = [RuntimeError("rate limit"), _ok_response()]
    mgr = LLMManager([
        LLMConfig(base_url="https://a/v1", model="m1", api_key="k1", router_enabled=False),
        LLMConfig(base_url="https://b/v1", model="m2", api_key="k2", router_enabled=False),
    ])
    reply = asyncio.run(mgr.complete("ping"))
    assert reply == "pong"
    assert mgr.last_used_backend == 1
    assert mgr.last_error is not None  # records the primary failure


def test_manager_prefers_live_over_mock(monkeypatch, fake_litellm):
    # mock-only primary must be skipped in favour of a live secondary
    mgr = LLMManager([
        LLMConfig(router_enabled=False),  # no creds -> mock
        LLMConfig(base_url="https://b/v1", model="m2", api_key="k2", router_enabled=False),
    ])
    reply = asyncio.run(mgr.complete("ping"))
    assert reply == "pong"
    assert mgr.last_used_backend == 1


def test_manager_mock_mode_reflects_all_backends():
    mgr = LLMManager([LLMConfig(router_enabled=False), LLMConfig(router_enabled=False)])
    assert mgr.mock_mode is True
    assert mgr.config is mgr.backends[0]


def test_build_manager_from_env_multi(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://primary/v1")
    monkeypatch.setenv("OPENAI_FALLBACK_PROVIDERS", "ollama,deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    mgr = build_manager_from_env()
    assert isinstance(mgr, LLMManager)
    assert len(mgr.backends) == 3  # primary + ollama + deepseek
    assert mgr.backends[1].base_url == "http://localhost:11434/v1"
    assert mgr.backends[2].base_url == "https://api.deepseek.com/v1"


def test_build_manager_single_when_no_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://p/v1")
    inst = build_manager_from_env()
    assert isinstance(inst, LLMEngine)


def test_build_default_llm_fails_over(monkeypatch):
    # Replace openai.OpenAI with a fake whose first call fails, second succeeds.
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = [
        RuntimeError("401"),
        _ok_response("hi"),
    ]
    monkeypatch.setattr("openai.OpenAI", lambda **k: fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://primary/v1")
    monkeypatch.setenv("OPENAI_FALLBACK_BASE_URLS", "https://fallback/v1")

    llm = __import__("huanxin.court.real_executor", fromlist=["build_default_llm"]).build_default_llm()
    assert llm is not None
    out = llm("ping")
    assert out == "hi"


def test_build_default_llm_all_fail_raises(monkeypatch):
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("dead")
    monkeypatch.setattr("openai.OpenAI", lambda **k: fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://primary/v1")

    re = __import__("huanxin.court.real_executor", fromlist=["build_default_llm"])
    llm = re.build_default_llm()
    assert llm is not None
    with pytest.raises(RuntimeError):
        llm("ping")
