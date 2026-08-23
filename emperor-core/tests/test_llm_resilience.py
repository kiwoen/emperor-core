"""Resilience tests for the multi-backend LLM layer (optimization pass).

Proves, without network:
- _resolve_backends_from_env honours provider ``model_env`` overrides
  (NVIDIA_MODEL / ARK_MODEL) so the self-evolution loop matches the emperor path
- LLMManager fails over with retry/backoff when a backend transiently errors
- Circuit breaker opens after ``failure_threshold`` consecutive failures and skips
- Per-backend rate-limit throttle enforces a minimum inter-call interval
- get_stats() exposes success/failure/latency/circuit telemetry
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import jarvis.core.llm as core_llm
from jarvis.core.llm import (
    LLMConfig,
    LLMManager,
    _resolve_backends_from_env,
    build_manager_from_env,
)


@pytest.fixture
def fake_litellm(monkeypatch):
    mock = MagicMock()
    mock.acompletion = AsyncMock()
    mock.acompletion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="pong"))]
    )
    monkeypatch.setattr(core_llm, "litellm", mock)
    return mock


def _ok(text="pong"):
    return MagicMock(choices=[MagicMock(message=MagicMock(content=text))])


# ── model_env consistency (the self-evolution bug fix) ───────────────────────

def test_resolve_backends_honors_nvidia_model_env(monkeypatch):
    for v in ["OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_FALLBACK_BASE_URLS"]:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("OPENAI_FALLBACK_PROVIDERS", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
    monkeypatch.setenv("NVIDIA_MODEL", "deepseek-ai/deepseek-r1")
    backends = _resolve_backends_from_env()
    nv = [b for b in backends if b.base_url == "https://integrate.api.nvidia.com/v1"]
    assert nv, "nvidia backend should be resolved"
    assert nv[0].model == "deepseek-ai/deepseek-r1"
    assert nv[0].requests_per_minute == 40  # registry throttle carried over


def test_resolve_backends_honors_ark_model_env(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_FALLBACK_PROVIDERS", "doubao")
    monkeypatch.setenv("ARK_API_KEY", "ark-x")
    monkeypatch.setenv("ARK_MODEL", "ep-abc123")
    backends = _resolve_backends_from_env()
    ark = [b for b in backends if b.base_url.startswith("https://ark.")]
    assert ark and ark[0].model == "ep-abc123"


def test_resolve_backends_skips_keyless_provider_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_FALLBACK_PROVIDERS", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    backends = _resolve_backends_from_env()
    assert not any(b.base_url == "https://api.deepseek.com/v1" for b in backends)


# ── retry / backoff ──────────────────────────────────────────────────────────

def test_retry_backoff_succeeds_after_transient_errors(fake_litellm):
    # engine retries internally (max_retries=2 -> 3 attempts) then succeeds
    fake_litellm.acompletion.side_effect = [
        RuntimeError("rate limit"),
        RuntimeError("rate limit"),
        _ok("recovered"),
    ]
    cfg = LLMConfig(base_url="https://a/v1", model="m", api_key="k",
                    router_enabled=False, max_retries=2)
    mgr = LLMManager([cfg])
    reply = asyncio.run(mgr.complete("ping"))
    assert reply == "recovered"
    assert fake_litellm.acompletion.call_count == 3
    assert mgr.last_used_backend == 0


# ── circuit breaker ───────────────────────────────────────────────────────────

def test_circuit_breaker_opens_after_threshold(monkeypatch, fake_litellm):
    monkeypatch.setenv("LLM_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("LLM_COOLDOWN_S", "120")
    fake_litellm.acompletion.side_effect = RuntimeError("dead")
    cfg = LLMConfig(base_url="https://a/v1", model="m", api_key="k", router_enabled=False)
    mgr = LLMManager([cfg])
    # 1st call: 1 failure (consecutive=1 < threshold)
    asyncio.run(mgr.complete("ping"))
    assert mgr.stats[0].circuit_open is False
    # 2nd call: 2nd failure -> threshold reached -> circuit opens
    asyncio.run(mgr.complete("ping"))
    assert mgr.stats[0].circuit_open is True
    assert mgr.stats[0].failure >= 2


def test_circuit_breaker_skips_open_backend(monkeypatch, fake_litellm):
    monkeypatch.setenv("LLM_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("LLM_COOLDOWN_S", "120")
    fake_litellm.acompletion.side_effect = RuntimeError("dead")
    cfg = LLMConfig(base_url="https://a/v1", model="m", api_key="k", router_enabled=False)
    mgr = LLMManager([cfg])
    asyncio.run(mgr.complete("ping"))  # 1 failure -> circuit opens
    assert mgr.stats[0].circuit_open is True
    # 2nd call: backend is skipped (circuit open); manager falls back to mock
    reply = asyncio.run(mgr.complete("ping"))
    assert isinstance(reply, str)  # best-effort mock, no exception


# ── rate-limit throttle ───────────────────────────────────────────────────────

def test_throttle_enforces_min_interval(monkeypatch, fake_litellm):
    sleep_calls = []

    async def _fake_sleep(s):
        sleep_calls.append(s)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    # 600 rpm -> min interval 0.1s
    cfg = LLMConfig(base_url="https://a/v1", model="m", api_key="k",
                    router_enabled=False, requests_per_minute=600)
    mgr = LLMManager([cfg])
    asyncio.run(mgr.complete("ping"))
    asyncio.run(mgr.complete("ping"))
    # first call sleeps (last_ts init 0 -> large wait -> clamped? no: wait negative -> skip)
    # second call: ~0.1s wait expected
    assert any(0.05 < s < 0.2 for s in sleep_calls), f"expected ~0.1s throttle, got {sleep_calls}"


# ── telemetry ─────────────────────────────────────────────────────────────────

def test_get_stats_telemetry(fake_litellm):
    cfg = LLMConfig(base_url="https://a/v1", model="m", api_key="k", router_enabled=False)
    mgr = LLMManager([cfg])
    asyncio.run(mgr.complete("ping"))
    stats = mgr.get_stats()
    assert stats["n_backends"] == 1
    assert stats["backends"][0]["success"] == 1
    assert stats["backends"][0]["failure"] == 0
    assert stats["last_used_backend"] == 0
    assert stats["last_used_model"] == "m"
    assert stats["backends"][0]["last_latency_ms"] >= 0


def test_get_stats_exposes_circuit_state(monkeypatch, fake_litellm):
    monkeypatch.setenv("LLM_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("LLM_COOLDOWN_S", "120")
    fake_litellm.acompletion.side_effect = RuntimeError("dead")
    cfg = LLMConfig(base_url="https://a/v1", model="m", api_key="k", router_enabled=False)
    mgr = LLMManager([cfg])
    asyncio.run(mgr.complete("ping"))
    stats = mgr.get_stats()
    assert stats["backends"][0]["circuit_open"] is True
    assert stats["backends"][0]["failure"] == 1
