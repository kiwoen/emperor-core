"""Tests for Hermes Agent MCP server and client."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from jarvis.hermes.bus import MessageBus, Topic
from jarvis.hermes_agent.server import HermesMCPServer, TOOLS_SCHEMA
from jarvis.hermes_agent.client import HermesMCPClient


# ── Mock stdio helper ───────────────────────────────────────────────────────

class MockStdioTransport:
    """Simulates MCP JSON-RPC over in-memory queues."""

    def __init__(self):
        self._queue = asyncio.Queue()
        self.server_output: list[str] = []

    async def readline(self) -> bytes:
        line = await self._queue.get()
        return line.encode("utf-8")

    def write(self, data: str) -> None:
        self.server_output.append(data.strip())

    # Drive: send a JSON-RPC request to the server
    async def send_rpc(self, method: str, params: dict | None = None, id_: int = 1) -> None:
        msg = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
        await self._queue.put(json.dumps(msg))

    # Collect server's output, parse as JSON
    def get_responses(self) -> list[dict]:
        return [json.loads(line) for line in self.server_output if line.strip()]

    def get_response(self, id_: int | None = None) -> dict | None:
        for r in self.get_responses():
            if r.get("id") == id_ or (id_ is None and "result" in r):
                return r
        return None


# ── HermesMCPServer ─────────────────────────────────────────────────────────

class TestHermesMCPServer:
    def test_tools_schema(self):
        """Verify the tools schema is well-formed."""
        assert "tools" in TOOLS_SCHEMA
        tool_names = [t["name"] for t in TOOLS_SCHEMA["tools"]]
        assert "herm_publish" in tool_names
        assert "herm_request" in tool_names
        assert "herm_subscribe_drain" in tool_names
        assert "herm_list_topics" in tool_names

    def test_mcp_response_format(self):
        from jarvis.hermes_agent.server import _mcp_response
        r = json.loads(_mcp_response(1, {"ok": True}))
        assert r["jsonrpc"] == "2.0"
        assert r["id"] == 1
        assert r["result"]["ok"] is True

    def test_mcp_error_format(self):
        from jarvis.hermes_agent.server import _mcp_error
        r = json.loads(_mcp_error(1, -32600, "Invalid"))
        assert "error" in r
        assert r["error"]["code"] == -32600


# ── HermesMCPServer tool execution ──────────────────────────────────────────

class TestHermesMCPServerTools:
    """Test each MCP tool individually via direct call (bypass stdio)."""

    @pytest.mark.asyncio
    async def test_tool_publish(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)

        # Subscribe to catch the message
        received = []
        bus.subscribe(lambda m: received.append(m), "test.topic")

        result = await server._tool_publish({
            "topic": "test.topic",
            "payload": {"msg": "hello"},
        })
        assert result["published"] is True
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].payload == {"msg": "hello"}

    @pytest.mark.asyncio
    async def test_tool_request(self):
        bus = MessageBus()

        # Register a handler that replies
        async def handler(msg):
            await bus.reply(msg, payload={"echo": msg.payload}, sender="test-handler")

        bus.subscribe(handler, "test.echo")

        server = HermesMCPServer(bus)
        result = await server._tool_request({
            "topic": "test.echo",
            "payload": {"x": 42},
            "timeout": 3,
        })
        assert "reply" in result
        assert result["reply"]["echo"]["x"] == 42

    @pytest.mark.asyncio
    async def test_tool_request_timeout(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)

        # No handler → request raises RuntimeError (no subscribers)
        result = await server._tool_request({
            "topic": "test.nobody",
            "payload": {},
            "timeout": 0.5,
        })
        assert "error" in result
        assert "No subscribers" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_subscribe_drain(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)

        from jarvis.hermes.bus import Message, MessageType

        # Publish a few messages on a timer
        async def publisher():
            for i in range(3):
                msg = Message(
                    topic=Topic("drain.test"),
                    type=MessageType.EVENT,
                    payload={"i": i},
                    sender="publisher",
                )
                await bus.publish(msg)
                await asyncio.sleep(0.05)

        asyncio.ensure_future(publisher())

        result = await server._tool_subscribe_drain({
            "topic_pattern": "drain.test",
            "duration": 0.5,
        })
        assert result["count"] == 3
        assert [m["payload"]["i"] for m in result["messages"]] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_tool_list_topics(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)

        # Register subscribers to populate pattern_index (topic_summary source)
        bus.subscribe(lambda m: None, "test.foo")
        bus.subscribe(lambda m: None, "test.bar")
        bus.subscribe(lambda m: None, "vscode.file.open")

        result = await server._tool_list_topics()
        assert result["count"] == 3
        assert "test.foo" in result["topics"]


# ── MCP JSON-RPC lifecycle ──────────────────────────────────────────────────

class TestMCPServerLifecycle:
    """Test JSON-RPC initialize/ping/tools/list via direct dispatch."""

    @pytest.mark.asyncio
    async def test_initialize(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)
        response = await server._handle({"method": "initialize", "id": 1, "params": {}})
        r = json.loads(response)
        assert r["result"]["serverInfo"]["name"] == "hermes-mcp"
        assert "tools" in r["result"]["capabilities"]

    @pytest.mark.asyncio
    async def test_ping(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)
        response = await server._handle({"method": "ping", "id": 2, "params": {}})
        r = json.loads(response)
        assert r["id"] == 2
        assert r["result"] == {}

    @pytest.mark.asyncio
    async def test_tools_list(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)
        response = await server._handle({"method": "tools/list", "id": 3, "params": {}})
        r = json.loads(response)
        assert len(r["result"]["tools"]) == 4

    @pytest.mark.asyncio
    async def test_resources_list(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)
        response = await server._handle({"method": "resources/list", "id": 4, "params": {}})
        r = json.loads(response)
        assert r["result"]["resources"] == []

    @pytest.mark.asyncio
    async def test_unknown_method(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)
        response = await server._handle({"method": "unknown/thing", "id": 5, "params": {}})
        r = json.loads(response)
        assert "error" in r
        assert r["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_tools_call_publish(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)

        received = []
        bus.subscribe(lambda m: received.append(m), "mcp.test")

        response = await server._handle({
            "method": "tools/call",
            "id": 6,
            "params": {
                "name": "herm_publish",
                "arguments": {"topic": "mcp.test", "payload": {"k": "v"}},
            },
        })
        r = json.loads(response)
        assert "content" in r["result"]

        await asyncio.sleep(0.05)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_tools_call_request(self):
        bus = MessageBus()

        async def handler(msg):
            await bus.reply(msg, payload={"status": "ok"}, sender="handler")

        bus.subscribe(handler, "mcp.echo")

        server = HermesMCPServer(bus)
        response = await server._handle({
            "method": "tools/call",
            "id": 7,
            "params": {
                "name": "herm_request",
                "arguments": {"topic": "mcp.echo", "payload": {"q": "?"}},
            },
        })
        r = json.loads(response)
        content_text = r["result"]["content"][0]["text"]
        reply = json.loads(content_text)
        assert reply["reply"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_tools_call_unknown(self):
        bus = MessageBus()
        server = HermesMCPServer(bus)
        response = await server._handle({
            "method": "tools/call",
            "id": 8,
            "params": {"name": "does_not_exist", "arguments": {}},
        })
        r = json.loads(response)
        assert "error" in r


# ── HermesMCPClient ─────────────────────────────────────────────────────────

class TestHermesMCPClient:
    @pytest.mark.asyncio
    async def test_register_and_start(self):
        """Test registering a server (without actually connecting to one)."""
        bus = MessageBus()
        client = HermesMCPClient(bus)
        client.register_server("test-svr", "echo", ["hello"])
        assert "test-svr" in client._servers

    @pytest.mark.asyncio
    async def test_unknown_server(self):
        bus = MessageBus()
        client = HermesMCPClient(bus)
        client._running = True
        client._sub_id = "fake"

        # Directly call the handler to test error path
        from jarvis.hermes.bus import Message, MessageType
        msg = Message(
            topic=Topic("mcp.client.unknown.list"),
            payload={},
            sender="test",
            type=MessageType.REQUEST,
            id="req-1",
        )
        await client._on_request(msg)

        # We can't easily capture the reply since no subscriber is waiting,
        # but the handler should not raise.
        client._running = False

    @pytest.mark.asyncio
    async def test_list_tools_integration(self):
        """Integration: client queries HermesMCPServer's tools via bus."""
        bus = MessageBus()

        # Simulate: client-side MCP process that echoes what HermesMCPServer says
        class EchoMCP:
            def __init__(self, bus):
                self.bus = bus

            async def start(self):
                pass

            async def stop(self):
                pass

            async def initialize(self):
                return {"serverInfo": {"name": "echo"}}

            async def list_tools(self):
                return [{"name": "echo.hello", "description": "Echo tool"}]

            async def call_tool(self, name, args):
                return {"echo": args}

        client = HermesMCPClient(bus)
        client._servers["echo"] = EchoMCP(bus)
        await client.start()

        # Simulate a request asking for tools list
        from jarvis.hermes.bus import Message, MessageType

        msg = Message(
            topic=Topic("mcp.client.echo.list"),
            payload={},
            sender="test",
            type=MessageType.REQUEST,
            id="req-list",
        )
        await client._on_request(msg)

        await client.shutdown()
        assert True  # No exception = OK


# ── Server + Client round-trip (bridged via bus) ────────────────────────────

class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_server_client_tool_discovery(self):
        """HermesMCPServer exposes tools → client discovers via Hermes."""
        bus = MessageBus()
        server = HermesMCPServer(bus)

        # Register a handler that acts as an external MCP client
        reply = await server._handle({
            "method": "tools/list",
            "id": 42,
            "params": {},
        })
        r = json.loads(reply) if isinstance(reply, str) else reply
        assert len(r["result"]["tools"]) == 4
        tool_names = [t["name"] for t in r["result"]["tools"]]
        assert "herm_publish" in tool_names

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """End-to-end: MCP client → server → Hermes → Codex.

        Simulates an external agent calling herm_request to trigger
        a code analysis, verifying the full pipeline works.
        """
        bus = MessageBus()
        server = HermesMCPServer(bus)

        # Register a mock Codex handler
        async def codex_handler(msg):
            await bus.reply(msg, payload={
                "language": "python",
                "functions": [{"name": "foo", "line": 1}],
                "issues": [],
            }, sender="codex")

        bus.subscribe(codex_handler, "codex.analyze.python")

        # MCP tools/call → herm_request → Codex
        response = await server._handle({
            "method": "tools/call",
            "id": 99,
            "params": {
                "name": "herm_request",
                "arguments": {
                    "topic": "codex.analyze.python",
                    "payload": {"code": "def foo(): pass", "language": "python"},
                    "timeout": 3,
                },
            },
        })
        r = json.loads(response) if isinstance(response, str) else response
        content_text = r["result"]["content"][0]["text"]
        reply = json.loads(content_text)
        assert reply["reply"]["language"] == "python"
        assert reply["reply"]["functions"][0]["name"] == "foo"
