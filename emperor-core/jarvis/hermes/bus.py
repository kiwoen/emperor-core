"""
Hermes Message Bus — core pub/sub + request/reply engine.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("jarvis.hermes")


class MessageType(Enum):
    EVENT = "event"          # fire-and-forget
    REQUEST = "request"      # expects a reply
    REPLY = "reply"          # response to a request
    SYSTEM = "system"        # internal system message


@dataclass
class Topic:
    """Hierarchical topic path, e.g. 'codex.review.completed'."""

    path: str

    def __post_init__(self) -> None:
        # Normalize: strip leading/trailing dots, collapse consecutive dots
        segments = [s for s in self.path.lower().split(".") if s]
        self.path = ".".join(segments)

    def matches(self, pattern: str) -> bool:
        return fnmatch.fnmatch(self.path, pattern)

    def __str__(self) -> str:
        return self.path

    def __hash__(self) -> int:
        return hash(self.path)


@dataclass
class Message:
    """A message on the Hermes bus."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    topic: Topic = field(default_factory=lambda: Topic("system.ping"))
    type: MessageType = MessageType.EVENT
    payload: Any = None
    sender: str = ""
    timestamp: float = field(default_factory=time.time)
    correlation_id: Optional[str] = None  # links request ↔ reply
    metadata: dict[str, Any] = field(default_factory=dict)

    def reply(self, payload: Any, sender: str = "") -> "Message":
        """Create a reply message linked to this request."""
        return Message(
            topic=self.topic,
            type=MessageType.REPLY,
            payload=payload,
            sender=sender,
            correlation_id=self.id,
        )


@dataclass
class Subscription:
    """A subscriber handle with optional filter."""

    callback: Callable[[Message], Coroutine[Any, Any, None]] = field(repr=False)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    topic_pattern: str = "*"
    once: bool = False  # auto-unsubscribe after first match

    def matches(self, topic: Topic) -> bool:
        return topic.matches(self.topic_pattern)


class MessageBus:
    """Asynchronous message bus for JARVIS internal communication.

    Features:
    - Topic-based pub/sub with wildcard patterns (fnmatch)
    - Request/reply with timeout
    - Pending reply tracking
    - Event sourcing via EventLog integration
    - Graceful shutdown
    """

    def __init__(self, event_log: Optional[Any] = None) -> None:
        self._subscriptions: dict[str, Subscription] = {}
        self._pattern_index: dict[str, set[str]] = defaultdict(set)  # pattern → sub_ids
        self._pending_replies: dict[str, asyncio.Future] = {}
        self._event_log = event_log
        self._running = False
        self._message_count: int = 0
        logger.info("Hermes MessageBus initialized")

    # ------------------------------------------------------------------
    # Pub/Sub
    # ------------------------------------------------------------------

    def subscribe(
        self,
        callback: Callable[[Message], Coroutine[Any, Any, None]],
        topic_pattern: str = "*",
        once: bool = False,
    ) -> Subscription:
        """Subscribe to messages matching a topic pattern.

        Patterns use fnmatch syntax: '*' matches any segment, '#' not supported.
        Examples: 'codex.*', '*.completed', 'orchestrator.intent.*'
        """
        sub = Subscription(topic_pattern=topic_pattern, callback=callback, once=once)
        self._subscriptions[sub.id] = sub
        self._pattern_index[topic_pattern].add(sub.id)
        logger.debug("Subscription %s → '%s'", sub.id, topic_pattern)
        return sub

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove a subscription by ID."""
        sub = self._subscriptions.pop(sub_id, None)
        if sub:
            self._pattern_index[sub.topic_pattern].discard(sub_id)
            if not self._pattern_index[sub.topic_pattern]:
                del self._pattern_index[sub.topic_pattern]
            logger.debug("Unsubscribed %s", sub_id)
            return True
        return False

    async def publish(self, message: Message) -> int:
        """Publish a message to all matching subscribers.

        Returns number of subscribers that received the message.
        """
        delivered = 0
        to_remove: list[str] = []

        for pattern, sub_ids in list(self._pattern_index.items()):
            if message.topic.matches(pattern):
                for sid in list(sub_ids):
                    sub = self._subscriptions.get(sid)
                    if sub is None:
                        to_remove.append(sid)
                        continue
                    try:
                        await sub.callback(message)
                        delivered += 1
                        if sub.once:
                            to_remove.append(sid)
                    except Exception:
                        logger.exception("Subscriber %s failed", sid)

        for sid in to_remove:
            self.unsubscribe(sid)

        self._message_count += 1
        if self._event_log:
            self._event_log.append(message)

        return delivered

    # ------------------------------------------------------------------
    # Request / Reply
    # ------------------------------------------------------------------

    async def request(
        self,
        topic: Topic,
        payload: Any,
        sender: str = "",
        timeout: float = 10.0,
    ) -> Message:
        """Send a request and wait for a single reply.

        Raises asyncio.TimeoutError if no reply within timeout.
        """
        msg = Message(
            topic=topic,
            type=MessageType.REQUEST,
            payload=payload,
            sender=sender,
        )
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_replies[msg.id] = future

        delivered = await self.publish(msg)
        if delivered == 0:
            self._pending_replies.pop(msg.id, None)
            raise RuntimeError(f"No subscribers for topic '{topic}'")

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_replies.pop(msg.id, None)
            raise

    async def reply(self, to_message: Message, payload: Any, sender: str = "") -> None:
        """Send a reply to a pending request."""
        reply_msg = to_message.reply(payload=payload, sender=sender)
        future = self._pending_replies.pop(to_message.id, None)
        if future and not future.done():
            future.set_result(reply_msg)
        else:
            # Reply arrived too late or no one waiting — publish as event
            reply_msg.type = MessageType.EVENT
            await self.publish(reply_msg)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        logger.info("Hermes MessageBus started")

    async def shutdown(self) -> None:
        """Graceful shutdown: cancel pending replies, clear subscriptions."""
        self._running = False
        for future in self._pending_replies.values():
            if not future.done():
                future.cancel()
        self._pending_replies.clear()
        self._subscriptions.clear()
        self._pattern_index.clear()
        logger.info("Hermes MessageBus shut down (%d messages total)", self._message_count)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def message_count(self) -> int:
        return self._message_count

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    def topic_summary(self) -> dict[str, int]:
        """Return {pattern: subscriber_count} summary."""
        return {p: len(sids) for p, sids in self._pattern_index.items()}
