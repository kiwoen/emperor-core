"""
JARVIS Multi-Model Executor.

Defines the executor protocol used by
:class:`jarvis.multi_model.MultiModelRouter` to turn one invocation request into
a :class:`~jarvis.multi_model.ParallelResult`.

Two implementations are provided:

* :class:`RealLLMExecutor` -- performs genuine LLM calls (via the New API relay
  when ``EMPEROR_RELAY_URL`` is set, otherwise per-provider through LiteLLM). It
  never fabricates output; on failure it returns ``success=False`` and records
  the error string. On unrecoverable mis-configuration (LiteLLM missing, no key
  / base URL resolved) it raises ``RuntimeError("backend unavailable: ...")`` so
  the router can fall back to the offline mock.
* :class:`OfflineMockExecutor` -- replicates the deterministic offline behaviour
  the router historically used for testing. It is explicitly offline and only
  auto-activates when no backend is configured.

All optional / network dependencies are import-guarded so this module (and the
router) import cleanly with no keys and no network.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

try:  # pragma: no cover - environment dependent
    import litellm  # type: ignore
except Exception:  # pragma: no cover
    litellm = None  # type: ignore

try:  # pragma: no cover - environment dependent
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

from jarvis.core.llm import FREE_PROVIDERS
from jarvis.multi_model import ModelConfig, ParallelResult

logger = logging.getLogger("jarvis.multi_model_executor")


# ══════════════════════════════════════════════════════════════════
# Executor protocol
# ══════════════════════════════════════════════════════════════════


class LLMExecutor:
    """Callable protocol for turning one invocation into a ParallelResult.

    Implementations are plain callables with the signature::

        __call__(self, messages, model, cached_latency) -> ParallelResult

    ``cached_latency`` is the router's last-known latency estimate for the model
    (used by the offline executor for determinism / parity with legacy code).
    Real executors ignore it and measure the true latency.
    """

    def __call__(
        self,
        messages: list[dict],
        model: "ModelConfig",
        cached_latency: float,
    ) -> "ParallelResult":
        raise NotImplementedError


# ══════════════════════════════════════════════════════════════════
# Offline mock executor (deterministic, offline-only)
# ══════════════════════════════════════════════════════════════════


class OfflineMockExecutor(LLMExecutor):
    """Deterministic, offline-only executor mirroring the legacy _simulate_call.

    This is the ONLY place where "simulated" output is allowed. It is selected
    automatically when no LLM backend is configured, and emits a one-time
    warning so operators know the calls are not real.
    """

    _warned = False

    def __call__(self, messages, model, cached_latency):
        if not OfflineMockExecutor._warned:
            OfflineMockExecutor._warned = True
            logger.warning(
                "MultiModelRouter: no LLM backend configured — using OFFLINE mock "
                "executor; calls are simulated, not real. Set EMPEROR_RELAY_URL or "
                "provider keys for genuine multi-model distillation."
            )

        prompt_text = str(messages[-1].get("content", "")) if messages else ""
        base_latency = cached_latency
        # Deterministic offline latency: baseline + stable jitter from the prompt.
        simulated_latency = base_latency + (hash(prompt_text) % 200)

        reasoning_note = " [reasoning enabled]" if model.supports_reasoning else ""
        output = (
            f"[{model.display_name}] Response to: {prompt_text[:80]}..."
            f"{reasoning_note}"
        )

        # ── Cost estimate (mirrors legacy formula) ──
        prompt_chars = len(prompt_text)
        est_input_tokens = max(prompt_chars // 4, 1)
        est_output_tokens = 200
        cost = (
            (est_input_tokens / 1000) * model.cost_per_1k_input
            + (est_output_tokens / 1000) * model.cost_per_1k_output
        )

        return ParallelResult(
            model_id=model.model_id,
            tier=model.tier,
            output=output,
            latency_ms=round(simulated_latency, 2),
            success=True,
            cost_estimate=round(cost, 6),
            tokens_in=est_input_tokens,
            tokens_out=est_output_tokens,
        )


# ══════════════════════════════════════════════════════════════════
# Real LLM executor (genuine calls)
# ══════════════════════════════════════════════════════════════════


class RealLLMExecutor(LLMExecutor):
    """Performs genuine LLM calls.

    Resolution priority:
      1. ``EMPEROR_RELAY_URL`` set  -> route ALL models through the OpenAI-
         compatible relay (New API) with ``Authorization: Bearer
         $EMPEROR_RELAY_KEY``; ``model`` is passed through verbatim (the relay
         does provider/format conversion). This is the "API 中转站" integration.
      2. Otherwise per-provider mapping via ``jarvis.core.llm.FREE_PROVIDERS``:
         * provider == "openai"    -> ``OPENAI_BASE_URL`` (or default) + ``OPENAI_API_KEY``
         * provider == "anthropic" -> ``anthropic/<model_id>`` via LiteLLM + ``ANTHROPIC_API_KEY``
         * else lookup ``FREE_PROVIDERS[provider]`` for base_url + key_env.

    Optional network deps are guarded. When the backend genuinely cannot be used
    (LiteLLM missing, no key/base URL) it raises ``RuntimeError("backend
    unavailable: ...")`` so the router can fall back to the offline mock. Any
    exception during a live call is returned as ``success=False`` (never faked).
    """

    def __init__(
        self,
        store: Any = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> None:
        self._store = store
        self.temperature = temperature
        self.max_tokens = max_tokens

    # ── Resolve backend for a single model ──

    def _resolve(self, model: "ModelConfig") -> dict:
        relay_url = os.getenv("EMPEROR_RELAY_URL", "").strip()
        if relay_url:
            return {
                "mode": "relay",
                "url": relay_url.rstrip("/"),
                "key": os.getenv("EMPEROR_RELAY_KEY", "").strip(),
            }

        provider = (model.provider or "").strip().lower()
        if provider == "openai":
            key = os.getenv("OPENAI_API_KEY", "").strip()
            base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
            if not key and not base_url:
                raise RuntimeError(
                    "backend unavailable: no OPENAI_API_KEY / OPENAI_BASE_URL for openai provider"
                )
            return {
                "mode": "litellm",
                "model": model.model_id,
                "api_key": key,
                "api_base": base_url,
            }
        if provider == "anthropic":
            key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise RuntimeError(
                    "backend unavailable: no ANTHROPIC_API_KEY for anthropic provider"
                )
            return {
                "mode": "litellm",
                "model": f"anthropic/{model.model_id}",
                "api_key": key,
                "api_base": None,
            }
        prov = FREE_PROVIDERS.get(provider)
        if prov is None:
            raise RuntimeError(f"backend unavailable: no provider config for {provider!r}")
        key_env = prov.get("key_env", "")
        key = os.getenv(key_env, "").strip() if key_env else ""
        base_url = prov.get("base_url", "")
        if key_env and not key:
            raise RuntimeError(
                f"backend unavailable: {key_env} not set for provider {provider!r}"
            )
        return {
            "mode": "litellm",
            "model": model.model_id,
            "api_key": key,
            "api_base": base_url,
        }

    # ── Invocation ──

    def __call__(self, messages, model, cached_latency):
        backend = self._resolve(model)
        prompt_text = str(messages[-1].get("content", "")) if messages else ""
        t0 = time.perf_counter()

        success = False
        output = ""
        error = ""
        used_in = 0
        used_out = 0

        try:
            if backend["mode"] == "relay":
                content, used_in, used_out = self._call_relay(backend, messages, model)
            else:
                content, used_in, used_out = self._call_litellm(backend, messages, model)
            output = content
            success = True
        except Exception as exc:  # noqa: BLE001 - never fabricate; surface as failure
            error = str(exc)
            success = False

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        cost_estimate = self._cost(model, used_in, used_out)

        if self._store is not None:
            self._record_trace(
                prompt_text, model, output, latency_ms, cost_estimate, success, error
            )

        return ParallelResult(
            model_id=model.model_id,
            tier=model.tier,
            output=output,
            latency_ms=latency_ms,
            success=success,
            error=error,
            cost_estimate=cost_estimate,
            tokens_in=used_in,
            tokens_out=used_out,
        )

    # ── Backend callers ──

    def _call_litellm(self, backend: dict, messages: list[dict], model: "ModelConfig"):
        if litellm is None:
            raise RuntimeError("backend unavailable: litellm is not installed")
        kwargs: dict[str, Any] = {
            "model": backend["model"],
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "api_key": backend["api_key"] or "sk-noauth",
            "timeout": 60,
        }
        if backend["api_base"]:
            kwargs["api_base"] = backend["api_base"]
        resp = litellm.completion(**kwargs)
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        used_in = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        used_out = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        return content, used_in, used_out

    def _call_relay(self, backend: dict, messages: list[dict], model: "ModelConfig"):
        url = backend["url"].rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if backend["key"]:
            headers["Authorization"] = f"Bearer {backend['key']}"
        payload = {
            "model": model.model_id,  # passed verbatim; relay does format conversion
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if httpx is not None:
            resp = httpx.post(url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        else:
            import json as _json
            import urllib.request

            req = urllib.request.Request(
                url,
                data=_json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = _json.loads(r.read().decode())
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        used_in = int(usage.get("prompt_tokens", 0) or 0)
        used_out = int(usage.get("completion_tokens", 0) or 0)
        return content, used_in, used_out

    # ── Helpers ──

    @staticmethod
    def _cost(model: "ModelConfig", used_in: int, used_out: int) -> float:
        return round(
            (used_in / 1000) * model.cost_per_1k_input
            + (used_out / 1000) * model.cost_per_1k_output,
            6,
        )

    def _record_trace(self, prompt_text, model, output, latency_ms, cost_estimate, success, error):
        try:
            from jarvis.learning.distillation_store import DistillationTrace

            self._store.record(
                DistillationTrace(
                    ts=time.time(),
                    prompt=prompt_text,
                    model_id=model.model_id,
                    tier=model.tier,
                    output=output,
                    latency_ms=latency_ms,
                    cost_estimate=cost_estimate,
                    success=success,
                    error=error,
                )
            )
        except Exception:  # pragma: no cover - tracing must never break a call
            logger.debug("distillation trace recording failed", exc_info=True)


# ══════════════════════════════════════════════════════════════════
# Default executor selection
# ══════════════════════════════════════════════════════════════════


def _is_backend_reachable() -> bool:
    """True when at least one genuine backend can be reached.

    Requires LiteLLM installed AND either a relay URL or a provider key present.
    """
    if litellm is None:
        return False
    if os.getenv("EMPEROR_RELAY_URL", "").strip():
        return True
    key_envs = [p.get("key_env") for p in FREE_PROVIDERS.values() if p.get("key_env")]
    key_envs += ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
    return any(os.getenv(k, "").strip() for k in key_envs)


def select_default_executor() -> "LLMExecutor":
    """Pick the executor to use when none is injected by the caller."""
    if _is_backend_reachable():
        return RealLLMExecutor()
    return OfflineMockExecutor()
