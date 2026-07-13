"""
Hermes MCP Server — exposes Hermes bus as MCP tools.

Implements the Model Context Protocol (MCP) over stdio, mapping MCP
tool calls to Hermes message bus operations.

MCP tools exposed:
- herm_publish: publish a message to a Hermes topic
- herm_request: send a request and await reply (with timeout)
- herm_subscribe_drain: subscribe and collect messages for N seconds
- herm_list_topics: list known active topics

Can be started via: `hermes mcp serve` (entry point)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.hermes.bus import MessageBus

logger = logging.getLogger("jarvis.hermes_agent.server")

# ── MCP JSON-RPC helpers ────────────────────────────────────────────────────

def _mcp_response(id_: Any, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": result})

def _mcp_error(id_: Any, code: int, message: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})

def _mcp_notification(method: str, params: dict[str, Any] | None = None) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})


# ── MCP Tool schema registry ────────────────────────────────────────────────

TOOLS_SCHEMA = {
    "tools": [
        {
            "name": "herm_publish",
            "description": "Publish a message to a Hermes bus topic. All subscribers to matching topics will receive it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Hermes topic path, e.g., 'vscode.file.open'"},
                    "payload": {"type": "object", "description": "Message payload as a JSON object"},
                },
                "required": ["topic", "payload"],
            },
        },
        {
            "name": "herm_request",
            "description": "Send a request to a Hermes topic and await a single reply (with timeout). Use for RPC-style interactions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Hermes topic path, e.g., 'codex.analyze.python'"},
                    "payload": {"type": "object", "description": "Request payload as a JSON object"},
                    "timeout": {"type": "number", "description": "Timeout in seconds (default: 10)"},
                },
                "required": ["topic", "payload"],
            },
        },
        {
            "name": "herm_subscribe_drain",
            "description": "Subscribe to a topic pattern and collect all messages received within a time window. Returns the collected messages.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic_pattern": {"type": "string", "description": "Topic pattern with wildcards, e.g., 'vscode.*' or 'codex.review.*'"},
                    "duration": {"type": "number", "description": "Collection window in seconds (default: 5)"},
                    "max_messages": {"type": "integer", "description": "Max messages to collect (default: 50)"},
                },
                "required": ["topic_pattern"],
            },
        },
        {
            "name": "herm_list_topics",
            "description": "List all currently active (known) Hermes topics. Useful for discovering available services.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]
}


class HermesMCPServer:
    """MCP server exposing Hermes bus as discoverable tools.

    Usage:
        bus = MessageBus(...)
        server = HermesMCPServer(bus)
        await server.serve_stdio()   # blocks, reading JSON-RPC from stdin
    """

    def __init__(self, bus: "MessageBus", name: str = "hermes-mcp") -> None:
        self.bus = bus
        self.name = name
        self._collected_msgs: list[dict] = []
        self._collect_sub_id: Optional[str] = None

    # ── JSON-RPC dispatch ───────────────────────────────────────────────────

    async def serve_stdio(self) -> None:
        """Read JSON-RPC from stdin, write to stdout. Blocks until stdin closes."""
        logger.info("Hermes MCP server started (stdio)")

        # Send initialize notification
        sys.stdout.write(_mcp_notification("initialized", {"server": self.name}) + "\n")
        sys.stdout.flush()

        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break

            try:
                request = json.loads(line.decode("utf-8").strip())
            except json.JSONDecodeError:
                continue

            response = await self._handle(request)
            if response:
                sys.stdout.write(response + "\n")
                sys.stdout.flush()

    async def _handle(self, request: dict) -> Optional[str]:
        method = request.get("method", "")
        id_ = request.get("id")
        params = request.get("params", {})

        # ── Lifecycle ──
        if method == "initialize":
            return _mcp_response(id_, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": self.name, "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })

        if method == "notifications/initialized":
            return None  # no response needed

        if method == "ping":
            return _mcp_response(id_, {})

        # ── Tools ──
        if method == "tools/list":
            return _mcp_response(id_, TOOLS_SCHEMA)

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result = await self._call_tool(tool_name, arguments)
                return _mcp_response(id_, {"content": [{"type": "text", "text": json.dumps(result)}]})
            except Exception as e:
                logger.exception("Tool call failed: %s", tool_name)
                return _mcp_error(id_, -32000, str(e))

        # ── Resources (placeholder) ──
        if method == "resources/list":
            return _mcp_response(id_, {"resources": []})

        # ── Unknown ──
        return _mcp_error(id_, -32601, f"Method not found: {method}")

    # ── Tool implementations ────────────────────────────────────────────────

    async def _call_tool(self, name: str, args: dict) -> dict[str, Any]:
        if name == "herm_publish":
            return await self._tool_publish(args)
        elif name == "herm_request":
            return await self._tool_request(args)
        elif name == "herm_subscribe_drain":
            return await self._tool_subscribe_drain(args)
        elif name == "herm_list_topics":
            return await self._tool_list_topics()
        else:
            raise ValueError(f"Unknown tool: {name}")

    async def _tool_publish(self, args: dict) -> dict:
        topic = args.get("topic", "")
        payload = args.get("payload", {})
        from jarvis.hermes.bus import Topic, Message, MessageType

        msg = Message(
            topic=Topic(topic),
            type=MessageType.EVENT,
            payload=payload,
            sender="hermes-mcp",
        )
        delivered = await self.bus.publish(msg)
        return {"published": True, "topic": topic, "delivered_to": delivered}

    async def _tool_request(self, args: dict) -> dict:
        topic = args.get("topic", "")
        payload = args.get("payload", {})
        timeout = float(args.get("timeout", 10))
        from jarvis.hermes.bus import Topic

        try:
            reply = await self.bus.request(
                Topic(topic), payload, sender="hermes-mcp", timeout=timeout
            )
            return {"reply": reply.payload, "sender": reply.sender, "topic": topic}
        except asyncio.TimeoutError:
            return {"error": "timeout", "topic": topic, "timeout": timeout}
        except RuntimeError as e:
            return {"error": str(e), "topic": topic}
        except Exception as e:
            return {"error": str(e), "topic": topic}

    async def _tool_subscribe_drain(self, args: dict) -> dict:
        topic_pattern = args.get("topic_pattern", "")
        duration = float(args.get("duration", 5))
        max_msgs = int(args.get("max_messages", 50))

        collected: list[dict] = []
        sub_ref: list = [None]  # mutable ref to hold sub id

        async def on_msg(msg):
            from datetime import datetime
            ts = datetime.fromtimestamp(msg.timestamp).isoformat() if msg.timestamp else None
            collected.append({
                "topic": msg.topic.path,
                "payload": msg.payload,
                "sender": msg.sender,
                "timestamp": ts,
            })

        sub = self.bus.subscribe(on_msg, topic_pattern)

        try:
            await asyncio.sleep(duration)
        finally:
            self.bus.unsubscribe(sub.id)

        return {
            "topic_pattern": topic_pattern,
            "duration": duration,
            "messages": collected[:max_msgs],
            "count": min(len(collected), max_msgs),
        }

    async def _tool_list_topics(self) -> dict:
        summary = self.bus.topic_summary()
        topics = sorted(summary.keys())
        return {"topics": topics, "count": len(topics), "detail": summary}


# ── Entry point ─────────────────────────────────────────────────────────────

async def _serve_stdio():
    """CLI entry point: `hermes mcp serve`"""
    from jarvis.hermes.bus import MessageBus

    bus = MessageBus()
    server = HermesMCPServer(bus, name="jarvis-hermes")
    await server.serve_stdio()

def serve():
    """Synchronous entry point for console_scripts."""
    asyncio.run(_serve_stdio())
