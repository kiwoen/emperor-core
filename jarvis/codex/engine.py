"""
Codex Engine — main entry point, connects to Hermes bus.

Handles: analyze, generate, review, refactor.
Delegates to sub-engines and routes replies.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, TYPE_CHECKING

from jarvis.hermes.bus import Message, MessageBus, MessageType, Topic

if TYPE_CHECKING:
    from jarvis.codex.analyzer import Analyzer
    from jarvis.codex.generator import Generator

logger = logging.getLogger("jarvis.codex")


class CodexEngine:
    """Code intelligence engine hot-pluggable into JARVIS via Hermes.

    Usage:
        engine = CodexEngine(bus, analyzer, generator)
        await engine.start()    # subscribes to codex.* topics
        # … other modules send requests via bus …
        await engine.shutdown()
    """

    def __init__(self, bus: MessageBus, analyzer: "Analyzer", generator: "Generator") -> None:
        self.bus = bus
        self.analyzer = analyzer
        self.generator = generator
        self._sub_id: Optional[str] = None
        self._running = False
        self._handlers: dict[str, Any] = {}

    async def start(self) -> None:
        """Subscribe to codex topics and start listening."""
        sub = self.bus.subscribe(self._on_message, "codex.*")
        self._sub_id = sub.id
        self._running = True

        # Register sub-topic handlers
        self._handlers = {
            "codex.analyze": self._handle_analyze,
            "codex.generate": self._handle_generate,
            "codex.review": self._handle_review,
            "codex.refactor": self._handle_refactor,
        }

        logger.info("CodexEngine started (sub=%s)", self._sub_id)

    async def shutdown(self) -> None:
        self._running = False
        if self._sub_id:
            self.bus.unsubscribe(self._sub_id)
            self._sub_id = None
        self._handlers.clear()
        logger.info("CodexEngine shut down")

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    async def _on_message(self, msg: Message) -> None:
        """Route incoming messages to sub-handlers by topic prefix."""
        if msg.type != MessageType.REQUEST:
            return

        for prefix, handler in self._handlers.items():
            if msg.topic.path.startswith(prefix):
                try:
                    result = await handler(msg)
                    await self.bus.reply(msg, payload=result, sender="codex")
                except Exception as exc:
                    logger.exception("Codex handler failed")
                    await self.bus.reply(
                        msg, payload={"error": str(exc), "handler": prefix}, sender="codex"
                    )
                return

        # Unknown sub-topic
        await self.bus.reply(
            msg,
            payload={"error": f"Unknown codex topic: {msg.topic.path}"},
            sender="codex",
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_analyze(self, msg: Message) -> dict[str, Any]:
        """Handle codex.analyze.* requests."""
        return self.analyzer.analyze(msg.payload)

    async def _handle_generate(self, msg: Message) -> dict[str, Any]:
        """Handle codex.generate.* requests."""
        return self.generator.generate(msg.payload)

    async def _handle_review(self, msg: Message) -> dict[str, Any]:
        """Handle codex.review.* requests."""
        diff = msg.payload.get("diff", "") if isinstance(msg.payload, dict) else str(msg.payload)
        return self.analyzer.review_diff(diff)

    async def _handle_refactor(self, msg: Message) -> dict[str, Any]:
        """Handle codex.refactor.* requests."""
        code = msg.payload.get("code", "") if isinstance(msg.payload, dict) else str(msg.payload)
        pattern = msg.payload.get("pattern", "cleanup") if isinstance(msg.payload, dict) else "cleanup"
        return self.generator.refactor(code, pattern)
