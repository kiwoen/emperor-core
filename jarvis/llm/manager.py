"""Multi-backend LLM manager for the pydantic LLM stack (emperor entry).

This is a thin facade over :class:`jarvis.core.llm.LLMManager` — the canonical
multi-backend implementation with OpenAI-compatible failover, the
``FREE_PROVIDERS`` free-model registry, and environment-driven configuration.

Keeping the pydantic :class:`LLMEngine` (with its function-calling loop and
streaming API, which ``tests/test_llm.py`` pins) fully intact, this module adds
the *same* multi-backend capability to the emperor entry point by delegating to
the core manager. That guarantees the emperor chain and the domains main chain
behave identically (same backends, same failover order, same mock fallback)
without duplicating the failover / registry logic.

Public API (drop-in compatible with ``LLMEngine`` for simple completion):
    manager.chat_sync(prompt, system="") -> str
    await manager.chat(prompt, system="") -> str
    async for chunk in manager.chat_stream(prompt, system=""): ...
    manager.config            -> pydantic LLMConfig (first backend)
    manager.mock_mode         -> bool
    manager.last_error        -> Optional[str]
    manager.last_used_backend -> Optional[int]
    manager.get_cost_report() -> dict
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any, AsyncIterator, Optional

from jarvis.llm.config import LLMConfig

logger = logging.getLogger("jarvis.llm.manager")


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync code, tolerant of an existing loop.

    Uses ``asyncio.run`` when no loop is running; otherwise runs the coroutine
    in a dedicated worker thread with its own event loop (avoids
    "cannot be called from a running event loop" errors).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


class LLMManager:
    """Multi-backend LLM manager delegating to ``jarvis.core.llm.LLMManager``.

    Construct from a list of pydantic :class:`LLMConfig` backends, or wrap a
    prebuilt core manager via :meth:`from_core` (used by
    :func:`build_manager_from_env`).
    """

    def __init__(
        self,
        backends: Optional[list[LLMConfig]] = None,
        *,
        core_obj: Any = None,
    ) -> None:
        import jarvis.core.llm as core

        if core_obj is not None:
            if not isinstance(core_obj, core.LLMManager):
                # core.build_manager_from_env() may return a single LLMEngine
                # when only one backend is configured; normalise to a manager.
                core_obj = core.LLMManager([core_obj.config])
            self._core = core_obj
            self._backends = [LLMConfig.from_core(b) for b in core_obj.backends]
        elif backends:
            self._backends = list(backends)
            self._core = core.LLMManager([b.to_core() for b in self._backends])
        else:
            raise ValueError("LLMManager requires either backends or core_obj")

    # ── Construction helpers ─────────────────────────────────────────

    @classmethod
    def from_core(cls, core_obj: Any) -> "LLMManager":
        """Wrap a prebuilt core ``LLMManager`` / ``LLMEngine``."""
        return cls(core_obj=core_obj)

    # ── Compatible surface with LLMEngine ───────────────────────────

    @property
    def config(self) -> LLMConfig:
        """First backend's pydantic config (for backward-compatible access)."""
        return self._backends[0]

    @property
    def backends(self) -> list[LLMConfig]:
        return list(self._backends)

    @property
    def mock_mode(self) -> bool:
        return self._core.mock_mode

    @property
    def last_error(self) -> Optional[str]:
        return self._core.last_error

    @property
    def last_used_backend(self) -> Optional[int]:
        return self._core.last_used_backend

    def get_cost_report(self) -> dict:
        return self._core.get_cost_report()

    def chat_sync(self, prompt: str, *, system: str = "", **_: Any) -> str:
        """Synchronous chat completion with multi-backend failover."""
        return _run_async(self._core.complete(prompt, system=system))

    async def chat(self, prompt: str, *, system: str = "", **_: Any) -> str:
        """Asynchronous chat completion with multi-backend failover."""
        return await self._core.complete(prompt, system=system)

    async def chat_stream(self, prompt: str, *, system: str = "", **_: Any) -> AsyncIterator[str]:
        """Best-effort streaming: yields the full completion as a single chunk.

        The core manager has no streaming API; callers that require true token
        streaming should use the single-backend pydantic ``LLMEngine`` directly.
        """
        text = await self._core.complete(prompt, system=system)
        yield text


def build_manager_from_env() -> LLMManager:
    """Build a multi-backend manager from OPENAI_* env (mirrors core).

    Delegates backend resolution (primary ``OPENAI_BASE_URL`` / ``OPENAI_ENV``
    tier, ``OPENAI_FALLBACK_*`` URLs, and ``OPENAI_FALLBACK_PROVIDERS`` free
    registry) to ``jarvis.core.llm.build_manager_from_env`` so the emperor
    entry uses exactly the same failover logic as the domains main chain.
    """
    import jarvis.core.llm as core

    core_obj = core.build_manager_from_env()
    return LLMManager.from_core(core_obj)
