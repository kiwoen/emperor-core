"""LLM engine — unified multi-provider interface via litellm.

Supports OpenAI, Anthropic, and Ollama backends with sync/async/streaming
and function calling (tool calling) through a single consistent API.

Function Calling loop:
    When ``tools`` and ``tool_registry`` are both provided, ``chat()`` and
    ``chat_sync()`` automatically detect ``tool_calls`` in the model response,
    execute them via the registry, feed results back, and continue generating
    (up to 5 rounds).
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Iterator, Optional, TYPE_CHECKING

from huanxin.llm.config import LLMConfig, ModelProvider

if TYPE_CHECKING:
    from huanxin.tools.registry import ToolRegistry

logger = logging.getLogger("huanxin.llm.engine")

# ── Constants ────────────────────────────────────────────────────────
_MAX_FC_ROUNDS = 5


class LLMEngine:
    """Unified LLM interface backed by litellm.

    Supports three providers with automatic routing via litellm:

    - ``openai``   — GPT-4o, GPT-4, GPT-3.5, etc.
    - ``anthropic`` — Claude 3 Opus, Sonnet, Haiku
    - ``ollama``   — Local models (llama3, mistral, etc.)

    Basic usage::

        config = LLMConfig(provider=ModelProvider.OPENAI, model_name="gpt-4o")
        engine = LLMEngine(config)

        # Sync
        reply = engine.chat_sync("Hello!")

        # Async
        reply = await engine.chat("Hello!")

        # Streaming
        async for chunk in engine.chat_stream("Tell me a story"):
            print(chunk, end="")

        # Function calling
        tools = [{"type": "function", "function": {...}}]
        reply = await engine.chat("What's the weather?", tools=tools)
    """

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config: LLMConfig = config or LLMConfig()
        self._litellm_available: bool = False
        self._check_litellm()

    # ── Private helpers ────────────────────────────────────────────────

    def _check_litellm(self) -> None:
        """Check if litellm is available; warn if not."""
        try:
            import litellm  # noqa: F401

            self._litellm_available = True
        except ImportError:
            logger.warning(
                "[LLMEngine] litellm not installed — install with: pip install litellm"
            )
            self._litellm_available = False

    def _build_kwargs(self, messages: list[dict], tools: Optional[list[dict]] = None) -> dict:
        """Build the common kwargs dict for litellm completion calls."""
        cfg = self.config
        # Resolve model string for litellm:
        # - openai: "openai/gpt-4o"
        # - anthropic: "anthropic/claude-3-opus-20240229"
        # - ollama: "ollama/llama3"
        model = f"{cfg.provider.value}/{cfg.model_name}"

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
        }

        if cfg.api_key:
            kwargs["api_key"] = cfg.api_key
        if cfg.base_url:
            kwargs["api_base"] = cfg.base_url
        if tools:
            kwargs["tools"] = tools
        if cfg.extra_params:
            kwargs.update(cfg.extra_params)

        return kwargs

    def _raise_if_unavailable(self) -> None:
        """Raise RuntimeError if litellm is not installed."""
        if not self._litellm_available:
            raise RuntimeError(
                "litellm is not installed. Run: pip install litellm"
            )

    # ── Sync API ───────────────────────────────────────────────────────

    def chat_sync(
        self,
        prompt: str,
        *,
        system: str = "",
        messages: Optional[list[dict]] = None,
        tools: Optional[list[dict]] = None,
        tool_registry: Optional["ToolRegistry"] = None,
        max_fc_rounds: int = _MAX_FC_ROUNDS,
    ) -> str:
        """Synchronous chat completion with automatic Function Calling loop.

        When both *tools* and *tool_registry* are provided, the engine
        automatically enters an FC loop:

        1. Sends prompt + tools to the model.
        2. If the model returns ``tool_calls``, executes each tool via
           *tool_registry* and appends results to the message list.
        3. Repeats up to *max_fc_rounds* times.
        4. Returns the final text response.

        Args:
            prompt: User message content.
            system: Optional system prompt.
            messages: Full message list (overrides prompt+system if provided).
            tools: Optional list of function/tool definitions.
            tool_registry: ``ToolRegistry`` for executing tool calls (required
                           for FC loop; ignored if tools is None).
            max_fc_rounds: Max FC loop iterations (default 5).

        Returns:
            Model response text.
        """
        self._raise_if_unavailable()

        import litellm

        if messages is not None:
            msg_list = list(messages)
        else:
            msg_list = []
            if system:
                msg_list.append({"role": "system", "content": system})
            msg_list.append({"role": "user", "content": prompt})

        # Fast path: no FC loop needed
        if not tools or tool_registry is None:
            kwargs = self._build_kwargs(msg_list, tools=tools)
            logger.debug("[LLMEngine] sync call: model=%s messages=%d", kwargs["model"], len(msg_list))
            try:
                response = litellm.completion(**kwargs)
                return self._extract_response(response)
            except Exception as e:
                logger.error("[LLMEngine] sync call failed: %s", e)
                raise

        # FC loop
        kwargs = self._build_kwargs(msg_list, tools=tools)
        for round_idx in range(1, max_fc_rounds + 1):
            logger.debug(
                "[LLMEngine] FC round %d/%d: model=%s messages=%d",
                round_idx, max_fc_rounds, kwargs["model"], len(msg_list),
            )
            try:
                response = litellm.completion(**kwargs)
            except Exception as e:
                logger.error("[LLMEngine] sync FC call failed: %s", e)
                raise

            choice = response.choices[0]
            # Check for tool calls
            if not (hasattr(choice.message, "tool_calls") and choice.message.tool_calls):
                return self._extract_response(response)

            # Append assistant message with tool calls
            msg_list.append({
                "role": "assistant",
                "content": choice.message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in choice.message.tool_calls
                ],
            })

            # Execute each tool call
            for tc in choice.message.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = tool_registry.execute_tool(tool_name, args)
                msg_list.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result.to_json(),
                })
                logger.debug(
                    "[LLMEngine] Tool '%s' executed: success=%s duration=%.1fms",
                    tool_name, result.success, result.duration_ms,
                )

            # Rebuild kwargs for next round
            kwargs = self._build_kwargs(msg_list, tools=tools)

        # Exhausted max rounds — extract final text (may be a tool call)
        return self._extract_text_from_messages(msg_list)

    # ── Async API ──────────────────────────────────────────────────────

    async def chat(
        self,
        prompt: str,
        *,
        system: str = "",
        messages: Optional[list[dict]] = None,
        tools: Optional[list[dict]] = None,
        tool_registry: Optional["ToolRegistry"] = None,
        max_fc_rounds: int = _MAX_FC_ROUNDS,
    ) -> str:
        """Asynchronous chat completion with automatic Function Calling loop.

        When both *tools* and *tool_registry* are provided, the engine
        automatically enters an FC loop:

        1. Sends prompt + tools to the model.
        2. If the model returns ``tool_calls``, executes each tool via
           *tool_registry* and appends results to the message list.
        3. Repeats up to *max_fc_rounds* times.
        4. Returns the final text response.

        Args:
            prompt: User message content.
            system: Optional system prompt.
            messages: Full message list (overrides prompt+system if provided).
            tools: Optional list of function/tool definitions.
            tool_registry: ``ToolRegistry`` for executing tool calls (required
                           for FC loop; ignored if tools is None).
            max_fc_rounds: Max FC loop iterations (default 5).

        Returns:
            Model response text.
        """
        self._raise_if_unavailable()

        import litellm

        if messages is not None:
            msg_list = list(messages)
        else:
            msg_list = []
            if system:
                msg_list.append({"role": "system", "content": system})
            msg_list.append({"role": "user", "content": prompt})

        # Fast path: no FC loop needed
        if not tools or tool_registry is None:
            kwargs = self._build_kwargs(msg_list, tools=tools)
            logger.debug("[LLMEngine] async call: model=%s messages=%d", kwargs["model"], len(msg_list))
            try:
                response = await litellm.acompletion(**kwargs)
                return self._extract_response(response)
            except Exception as e:
                logger.error("[LLMEngine] async call failed: %s", e)
                raise

        # FC loop
        kwargs = self._build_kwargs(msg_list, tools=tools)
        for round_idx in range(1, max_fc_rounds + 1):
            logger.debug(
                "[LLMEngine] FC async round %d/%d: model=%s messages=%d",
                round_idx, max_fc_rounds, kwargs["model"], len(msg_list),
            )
            try:
                response = await litellm.acompletion(**kwargs)
            except Exception as e:
                logger.error("[LLMEngine] async FC call failed: %s", e)
                raise

            choice = response.choices[0]
            # Check for tool calls
            if not (hasattr(choice.message, "tool_calls") and choice.message.tool_calls):
                return self._extract_response(response)

            # Append assistant message with tool calls
            msg_list.append({
                "role": "assistant",
                "content": choice.message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in choice.message.tool_calls
                ],
            })

            # Execute each tool call
            for tc in choice.message.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = tool_registry.execute_tool(tool_name, args)
                msg_list.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result.to_json(),
                })
                logger.debug(
                    "[LLMEngine] Tool '%s' executed: success=%s duration=%.1fms",
                    tool_name, result.success, result.duration_ms,
                )

            # Rebuild kwargs for next round
            kwargs = self._build_kwargs(msg_list, tools=tools)

        # Exhausted max rounds — extract final text from messages
        return self._extract_text_from_messages(msg_list)

    # ── Streaming API ──────────────────────────────────────────────────

    async def chat_stream(
        self,
        prompt: str,
        *,
        system: str = "",
        messages: Optional[list[dict]] = None,
        tools: Optional[list[dict]] = None,
    ) -> AsyncIterator[str]:
        """Asynchronous streaming chat completion.

        Yields content delta chunks as they arrive from the model.

        Args:
            prompt: User message content.
            system: Optional system prompt.
            messages: Full message list (overrides prompt+system if provided).
            tools: Optional list of function/tool definitions.

        Yields:
            Text chunks from the streaming response.
        """
        self._raise_if_unavailable()

        import litellm

        if messages is not None:
            msg_list = list(messages)
        else:
            msg_list = []
            if system:
                msg_list.append({"role": "system", "content": system})
            msg_list.append({"role": "user", "content": prompt})

        kwargs = self._build_kwargs(msg_list, tools=tools)
        kwargs["stream"] = True
        logger.debug("[LLMEngine] stream call: model=%s messages=%d", kwargs["model"], len(msg_list))

        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except Exception as e:
            logger.error("[LLMEngine] stream call failed: %s", e)
            raise

    # ── Response extraction ────────────────────────────────────────────

    @staticmethod
    def _extract_response(response: Any) -> str:
        """Extract the final text from a litellm completion response.

        Handles both plain text responses and tool-call responses.
        """
        choice = response.choices[0]

        # Tool call response → return as JSON string
        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            tool_calls = []
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": getattr(tc, "id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })
            return json.dumps(tool_calls, ensure_ascii=False)

        # Plain text response
        content = choice.message.content
        return content if content else ""

    @staticmethod
    def _extract_text_from_messages(msg_list: list[dict]) -> str:
        """Extract the last assistant text response from the message list.

        Used as a fallback when the FC loop exhausts all rounds.
        """
        for msg in reversed(msg_list):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return ""
