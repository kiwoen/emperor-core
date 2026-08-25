"""
MCP Manager — 多 MCP Server 统一管理器。

管理 MCP Client 生命周期，提供统一工具发现与调用接口。
内置 3 个模拟 MCP Server（无需外部进程），用于本地开发与测试。

Architecture:
    MCPManager      — 统一管理多个 MCP Client
    MockFilesystemServer — 模拟文件系统操作
    MockWebSearchServer  — 模拟 Web 搜索结果
    MockCalculatorServer — 数学计算

Usage:
    from huanxin.mcp_manager import MCPManager

    mgr = MCPManager()
    mgr.register_server(filesystem_config)
    tools = mgr.get_all_tools()
    result = mgr.discover_and_call("read_file", {"path": "/tmp/test.txt"})
"""

from __future__ import annotations

import json
import logging
import os
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from huanxin.mcp_client import (
    MCPClient,
    MCPTool,
    MCPServerConfig,
    MCPConnectionError,
    MCPToolError,
    MCPError,
)

logger = logging.getLogger("huanxin.mcp_manager")


# ═══════════════════════════════════════════════════════════════════
# Mock Server 基类
# ═══════════════════════════════════════════════════════════════════


@dataclass
class _MockToolDef:
    """模拟工具内部定义。"""
    tool: MCPTool
    handler: Callable[..., Any]


class _MockServerBase:
    """模拟 MCP Server 基类。

    子类只需注册工具定义即可。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._tools: Dict[str, _MockToolDef] = {}
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def register_tool(
        self,
        name: str,
        description: str,
        handler: Callable[..., Any],
        parameters_schema: Optional[dict] = None,
    ) -> None:
        self._tools[name] = _MockToolDef(
            tool=MCPTool(
                name=name,
                description=description,
                parameters_schema=parameters_schema or {},
            ),
            handler=handler,
        )

    def list_tools(self) -> list[MCPTool]:
        return [td.tool for td in self._tools.values()]

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用工具并返回 MCP 标准格式结果。"""
        td = self._tools.get(tool_name)
        if td is None:
            raise MCPToolError(
                f"Tool '{tool_name}' not found on server '{self.name}'"
            )
        try:
            result_data = td.handler(**arguments)
        except TypeError as e:
            raise MCPToolError(
                f"Tool '{tool_name}' parameter error: {e}"
            ) from e
        except Exception as e:
            raise MCPToolError(
                f"Tool '{tool_name}' execution error: {e}"
            ) from e

        return {
            "result": result_data,
            "raw": {
                "content": [{"type": "text", "text": str(result_data)}],
                "isError": False,
            },
        }


# ═══════════════════════════════════════════════════════════════════
# Mock FileSystem Server
# ═══════════════════════════════════════════════════════════════════

class MockFileSystemServer(_MockServerBase):
    """模拟文件系统操作 MCP Server。

    Tools:
        - list_dir(path)      → 列出目录内容
        - read_file(path)     → 读取文件内容（模拟）
        - write_file(path, content) → 写入文件（模拟到内存）
    """

    def __init__(self) -> None:
        super().__init__("filesystem-server")
        self._files: Dict[str, str] = {}  # 内存文件系统

        self.register_tool(
            "list_dir",
            "List contents of a directory",
            self._list_dir,
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list",
                    },
                },
                "required": ["path"],
            },
        )
        self.register_tool(
            "read_file",
            "Read file content",
            self._read_file,
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to read",
                    },
                },
                "required": ["path"],
            },
        )
        self.register_tool(
            "write_file",
            "Write content to a file",
            self._write_file,
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write",
                    },
                },
                "required": ["path", "content"],
            },
        )

    def _list_dir(self, path: str = ".") -> str:
        if path in (".", "./", ""):
            return json.dumps([
                {"name": "README.md", "type": "file", "size": 1024},
                {"name": "src", "type": "dir"},
                {"name": "tests", "type": "dir"},
                {"name": "config.json", "type": "file", "size": 256},
            ])
        if path in ("src", "./src"):
            return json.dumps([
                {"name": "main.py", "type": "file", "size": 2048},
                {"name": "utils.py", "type": "file", "size": 512},
            ])
        return json.dumps([])

    def _read_file(self, path: str = "") -> str:
        if path in self._files:
            return self._files[path]
        # 模拟一些已知文件
        known = {
            "README.md": "# 幻炘AI\n\nAn evolutionary AI system with MCP support.",
            "config.json": '{"version": "1.0", "mcp_enabled": true}',
            "src/main.py": "def main():\n    print('Hello, Huanxin!')",
        }
        return known.get(path, f"[Mock] Content of {path} (simulated)")

    def _write_file(self, path: str = "", content: str = "") -> str:
        self._files[path] = content
        return f"Written {len(content)} bytes to {path}"


# ═══════════════════════════════════════════════════════════════════
# Mock Web Search Server
# ═══════════════════════════════════════════════════════════════════

class MockWebSearchServer(_MockServerBase):
    """模拟 Web 搜索 MCP Server。

    Tools:
        - search(query, num_results) → 返回模拟搜索结果
    """

    _MOCK_RESULTS = [
        {
            "title": "MCP Protocol Specification",
            "url": "https://modelcontextprotocol.io/spec",
            "snippet": "The Model Context Protocol (MCP) is an open protocol...",
        },
        {
            "title": "Understanding AI Agents in 2025",
            "url": "https://example.com/ai-agents-2025",
            "snippet": "AI Agents are transforming enterprise automation...",
        },
        {
            "title": "Python AsyncIO Best Practices",
            "url": "https://docs.python.org/3/library/asyncio.html",
            "snippet": "asyncio is a library to write concurrent code...",
        },
    ]

    def __init__(self) -> None:
        super().__init__("web-search-server")

        self.register_tool(
            "search",
            "Search the web for information",
            self._search,
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results (default 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        )

    def _search(self, query: str = "", num_results: int = 5) -> str:
        results = self._MOCK_RESULTS[: min(num_results, len(self._MOCK_RESULTS))]
        # 给每条结果打上 query 标记
        return json.dumps([
            {**r, "query": query} for r in results
        ])


# ═══════════════════════════════════════════════════════════════════
# Mock Calculator Server
# ═══════════════════════════════════════════════════════════════════

class MockCalculatorServer(_MockServerBase):
    """模拟数学计算 MCP Server。

    Tools:
        - add(a, b)         → 加法
        - subtract(a, b)    → 减法
        - multiply(a, b)    → 乘法
        - divide(a, b)      → 除法
        - power(base, exp)  → 幂运算
        - sqrt(x)           → 平方根
    """

    def __init__(self) -> None:
        super().__init__("calculator-server")

        self.register_tool(
            "add",
            "Add two numbers",
            self._add,
            parameters_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        )
        self.register_tool(
            "subtract",
            "Subtract two numbers",
            self._subtract,
            parameters_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        )
        self.register_tool(
            "multiply",
            "Multiply two numbers",
            self._multiply,
            parameters_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        )
        self.register_tool(
            "divide",
            "Divide two numbers",
            self._divide,
            parameters_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        )
        self.register_tool(
            "power",
            "Compute base raised to the power of exp",
            self._power,
            parameters_schema={
                "type": "object",
                "properties": {
                    "base": {"type": "number"},
                    "exp": {"type": "number"},
                },
                "required": ["base", "exp"],
            },
        )
        self.register_tool(
            "sqrt",
            "Compute square root of a number",
            self._sqrt,
            parameters_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                },
                "required": ["x"],
            },
        )

    @staticmethod
    def _add(a: float = 0, b: float = 0) -> str:
        return f"{a} + {b} = {a + b}"

    @staticmethod
    def _subtract(a: float = 0, b: float = 0) -> str:
        return f"{a} - {b} = {a - b}"

    @staticmethod
    def _multiply(a: float = 0, b: float = 0) -> str:
        return f"{a} * {b} = {a * b}"

    def _divide(self, a: float = 0, b: float = 1) -> str:
        if b == 0:
            raise MCPToolError("Division by zero")
        return f"{a} / {b} = {a / b}"

    @staticmethod
    def _power(base: float = 0, exp: float = 1) -> str:
        return f"{base} ^ {exp} = {base ** exp}"

    @staticmethod
    def _sqrt(x: float = 0) -> str:
        if x < 0:
            raise MCPToolError("Cannot compute sqrt of negative number")
        return f"sqrt({x}) = {math.sqrt(x)}"


# ═══════════════════════════════════════════════════════════════════
# MCPManager
# ═══════════════════════════════════════════════════════════════════


class MCPManager:
    """MCP 管理器 — 统一管理多个 MCP Server。

    提供：
      - 注册 / 注销 Server
      - 聚合所有工具
      - 自动发现工具所在 Server 并调用
      - 内置 3 个模拟 Server 快捷注册

    >>> mgr = MCPManager()
    >>> mgr.register_builtin_mock_servers()
    >>> tools = mgr.get_all_tools()
    >>> result = mgr.discover_and_call("add", {"a": 1, "b": 2})
    """

    def __init__(self, client: Optional[MCPClient] = None) -> None:
        self._client = client or MCPClient()
        # server_name → server_config or mock_server_instance
        self._servers: Dict[str, Any] = {}
        # Prefix registry: tool_name → server_name
        self._tool_index: Dict[str, str] = {}

    @property
    def client(self) -> MCPClient:
        return self._client

    @property
    def server_count(self) -> int:
        return len(self._servers)

    # ── register_server ─────────────────────────────────────────

    def register_server(self, config: MCPServerConfig) -> bool:
        """注册一个 MCP Server（通过配置连接）。

        连接真实的 MCP Server（stdio 或 HTTP 传输）。
        """
        if config.name in self._servers:
            raise MCPConnectionError(
                f"Server '{config.name}' already registered"
            )

        self._client.connect(config)
        self._servers[config.name] = config

        # 索引工具
        tools = self._client.list_tools(config.name)
        for tool in tools:
            self._tool_index[tool.name] = config.name

        logger.info(
            "MCPManager registered server '%s' with %d tools",
            config.name, len(tools),
        )
        return True

    def register_mock_server(self, server: _MockServerBase) -> bool:
        """注册一个模拟 MCP Server（直接函数调用，无需外部进程）。"""
        if server.name in self._servers:
            raise MCPConnectionError(
                f"Server '{server.name}' already registered"
            )

        server.connect()
        self._servers[server.name] = server

        tools = server.list_tools()
        for tool in tools:
            self._tool_index[tool.name] = server.name

        logger.info(
            "MCPManager registered mock server '%s' with %d tools",
            server.name, len(tools),
        )
        return True

    # ── register_builtin_mock_servers ───────────────────────────

    def register_builtin_mock_servers(self) -> dict[str, _MockServerBase]:
        """一键注册 3 个内置模拟 Server。

        Returns:
            {server_name: server_instance} 映射。
        """
        servers = {
            "filesystem-server": MockFileSystemServer(),
            "web-search-server": MockWebSearchServer(),
            "calculator-server": MockCalculatorServer(),
        }
        for srv in servers.values():
            if srv.name not in self._servers:
                self.register_mock_server(srv)
        return servers

    # ── unregister_server ───────────────────────────────────────

    def unregister_server(self, name: str) -> None:
        """注销指定 Server。"""
        if name not in self._servers:
            raise MCPConnectionError(
                f"Server '{name}' is not registered"
            )

        entry = self._servers[name]

        # 从索引中移除
        to_remove: list[str] = []
        for tool_name, srv_name in self._tool_index.items():
            if srv_name == name:
                to_remove.append(tool_name)
        for tn in to_remove:
            del self._tool_index[tn]

        # 断开连接
        if isinstance(entry, MCPServerConfig):
            self._client.disconnect(name)
        elif isinstance(entry, _MockServerBase):
            entry.disconnect()

        del self._servers[name]
        logger.info("MCPManager unregistered server '%s'", name)

    # ── get_all_tools ───────────────────────────────────────────

    def get_all_tools(self) -> list[MCPTool]:
        """聚合所有已注册 Server 的工具列表。

        Returns:
            所有 MCPTool 的扁平列表。
        """
        all_tools: list[MCPTool] = []
        for srv_name, entry in self._servers.items():
            if isinstance(entry, _MockServerBase):
                all_tools.extend(entry.list_tools())
            elif isinstance(entry, MCPServerConfig):
                all_tools.extend(self._client.list_tools(srv_name))
        return all_tools

    def get_tools_by_server(self) -> dict[str, list[MCPTool]]:
        """按 Server 分组的工具列表。"""
        result: dict[str, list[MCPTool]] = {}
        for srv_name, entry in self._servers.items():
            if isinstance(entry, _MockServerBase):
                result[srv_name] = entry.list_tools()
            elif isinstance(entry, MCPServerConfig):
                result[srv_name] = self._client.list_tools(srv_name)
        return result

    # ── discover_and_call ───────────────────────────────────────

    def discover_and_call(
        self, tool_name: str, arguments: dict,
    ) -> dict:
        """自动发现工具所在 Server 并调用。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数。

        Returns:
            工具执行结果。

        Raises:
            MCPToolError: 工具未找到或调用失败。
        """
        if tool_name not in self._tool_index:
            raise MCPToolError(
                f"Tool '{tool_name}' not found in any registered server. "
                f"Available tools: {list(self._tool_index.keys())}"
            )

        server_name = self._tool_index[tool_name]
        entry = self._servers.get(server_name)

        if entry is None:
            raise MCPToolError(
                f"Server '{server_name}' for tool '{tool_name}' "
                f"not found (inconsistent state)"
            )

        if isinstance(entry, _MockServerBase):
            result = entry.call_tool(tool_name, arguments)
        elif isinstance(entry, MCPServerConfig):
            result = self._client.call_tool(server_name, tool_name, arguments)
        else:
            raise MCPToolError(
                f"Unknown server type for '{server_name}'"
            )

        logger.info(
            "MCPManager called '%s' on '%s' — success",
            tool_name, server_name,
        )
        return result

    # ── list_servers ────────────────────────────────────────────

    def list_servers(self) -> list[str]:
        """列出所有已注册的 Server 名称。"""
        return list(self._servers.keys())

    # ── shutdown ────────────────────────────────────────────────

    def shutdown(self) -> None:
        """关闭所有 Server 连接。"""
        names = list(self._servers.keys())
        for name in names:
            try:
                self.unregister_server(name)
            except Exception:
                logger.warning("Error shutting down '%s'", name)
        self._client.shutdown()
