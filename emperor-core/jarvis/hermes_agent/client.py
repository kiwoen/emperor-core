"""
Hermes MCP Client — connect Hermes bus to external MCP servers.

Allows JARVIS modules to call external MCP tools via Hermes topics.
Acts as a bridge from Hermes → MCP (stdio or HTTP transport).

Architecture:
    ┌──────────────┐   Hermes Bus    ┌──────────────┐   MCP/stdio   ┌───────────┐
    │  JARVIS       │◄──────────────►│ HermesMCP     │◄────────────►│ External   │
    │  (Orchestrator)│               │ Client        │              │ MCP Server │
    └──────────────┘                └──────────────┘              └───────────┘

Hermes topics:
    mcp.client.<server>/tools/list    → list external tools
    mcp.client.<server>/tools/call    → call external tool
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.hermes.bus import MessageBus

logger = logging.getLogger("jarvis.hermes_agent.client")


class MCPProcess:
    """Manages an MCP server subprocess over stdio JSON-RPC."""

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None) -> None:
        self.command = command
        self.args = args
        self.env = env
        self._proc: Optional[subprocess.Popen] = None
        self._req_id = 0

    async def start(self) -> None:
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**__import__("os").environ, **(self.env or {})},
            text=True,
        )

    async def stop(self) -> None:
        if self._proc:
            self._proc.stdin.close()
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    async def initialize(self) -> dict:
        return await self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "jarvis-hermes", "version": "1.0.0"},
        })

    async def list_tools(self) -> list[dict]:
        result = await self._rpc("tools/list", {})
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> Any:
        return await self._rpc("tools/call", {"name": name, "arguments": arguments})

    async def _rpc(self, method: str, params: dict) -> Any:
        if not self._proc:
            raise RuntimeError("MCP process not started")
        self._req_id += 1
        req = {"jsonrpc": "2.0", "id": self._req_id, "method": method, "params": params}
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()

        line = await asyncio.get_event_loop().run_in_executor(
            None, self._proc.stdout.readline
        )
        if not line:
            raise ConnectionError("MCP process closed connection")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp.get("result", {})


class HermesMCPClient:
    """Bridges Hermes bus → external MCP servers.

    Subscribes to mcp.client.* topics and forwards requests to
    the configured MCP server(s).

    Usage:
        client = HermesMCPClient(bus)
        client.register_server("codex", "codex", ["mcp-server"])
        await client.start()
    """

    def __init__(self, bus: "MessageBus") -> None:
        self.bus = bus
        self._servers: dict[str, MCPProcess] = {}
        self._sub_id: Optional[str] = None
        self._running = False

    def register_server(self, name: str, command: str, args: list[str],
                        env: dict[str, str] | None = None) -> None:
        """Register an external MCP server to bridge.

        Args:
            name: Logical name (used in Hermes topics: mcp.client.{name}/...)
            command: Executable (e.g., 'codex', 'python')
            args: Arguments (e.g., ['mcp-server'])
            env: Optional environment variables
        """
        self._servers[name] = MCPProcess(command, args, env)
        logger.info("Registered MCP server '%s': %s %s", name, command, " ".join(args))

    async def start(self) -> None:
        """Start all registered MCP servers and subscribe to Hermes."""
        for name, proc in self._servers.items():
            await proc.start()
            init = await proc.initialize()
            logger.info("MCP server '%s' initialized: %s", name, init.get("serverInfo", {}))

        sub = self.bus.subscribe(self._on_request, "mcp.client.*")
        self._sub_id = sub.id
        self._running = True
        logger.info("HermesMCPClient started (%d servers)", len(self._servers))

    async def shutdown(self) -> None:
        self._running = False
        if self._sub_id:
            self.bus.unsubscribe(self._sub_id)
            self._sub_id = None
        for name, proc in self._servers.items():
            await proc.stop()
        self._servers.clear()
        logger.info("HermesMCPClient shut down")

    async def _on_request(self, msg) -> None:
        from jarvis.hermes.bus import MessageType

        if msg.type != MessageType.REQUEST:
            return

        topic_parts = msg.topic.path.split(".")
        # mcp.client.<server>/<action>
        if len(topic_parts) < 3:
            return

        server = topic_parts[2]
        action = topic_parts[3] if len(topic_parts) > 3 else "list"

        proc = self._servers.get(server)
        if not proc:
            await self.bus.reply(msg, payload={
                "error": f"Unknown server: {server}",
                "available": list(self._servers.keys()),
            }, sender="mcp-client")
            return

        try:
            if action == "list":
                tools = await proc.list_tools()
                await self.bus.reply(msg, payload={"tools": tools, "server": server}, sender="mcp-client")
            elif action == "call":
                tool_name = msg.payload.get("tool", "")
                arguments = msg.payload.get("arguments", {})
                result = await proc.call_tool(tool_name, arguments)
                await self.bus.reply(msg, payload={"result": result, "server": server}, sender="mcp-client")
            else:
                await self.bus.reply(msg, payload={"error": f"Unknown action: {action}"}, sender="mcp-client")
        except Exception as e:
            logger.exception("MCP client call failed for %s/%s", server, action)
            await self.bus.reply(msg, payload={"error": str(e)}, sender="mcp-client")
