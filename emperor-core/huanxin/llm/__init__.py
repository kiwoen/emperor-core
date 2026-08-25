"""LLM module — unified multi-provider LLM interface.

Exports:
    - ``LLMEngine`` — sync/async/streaming chat engine backed by litellm.
    - ``LLMConfig`` — pydantic configuration model for LLM backends.
    - ``ModelProvider`` — enum of supported providers (openai / anthropic / ollama).
    - ``LLMManager`` — multi-backend manager with failover (delegates to
      ``huanxin.core.llm`` so the emperor entry shares the domains main chain's
      failover / free-provider registry / env config).
    - ``build_manager_from_env`` — build a manager from OPENAI_* env vars.
"""

from huanxin.llm.config import LLMConfig, ModelProvider
from huanxin.llm.engine import LLMEngine
from huanxin.llm.manager import LLMManager, build_manager_from_env

__all__ = [
    "LLMEngine",
    "LLMConfig",
    "ModelProvider",
    "LLMManager",
    "build_manager_from_env",
]

