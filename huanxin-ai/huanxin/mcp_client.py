"""
MCP (Model Context Protocol) Client — Anthropic MCP 协议客户端实现。

实现 JSON-RPC 2.0 格式的 MCP 协议，支持 stdio 和 HTTP 两种传输方式。
目标：让 Agent 能够连接外部 MCP Server 并调用其工具，解决"Agent 集成复杂度爆炸"问题。

Architecture:
    MCPServerConfig  — 服务器连接配置
    MCPTool          — 工具定义
    MCPClient        — 协议客户端，管理连接生命周期与工具调用

Protocol (JSON-RPC 2.0):
    initialize    → 握手协商
    tools/list    → 获取工具列表
    tools/call    → 执行工具调用

Usage:
    from huanxin.mcp_client import MCPClient, MCPServerConfig

    client = MCPClient()
    config = MCPServerConfig(
        name="filesystem-server",
        transport="stdio",
        command="python", args=["-m", "mcp_server"],
    )
    client.connect(config)
    tools = client.list_tools("filesystem-server")
    result = client.call_tool("filesystem-server", "read_file", {"path": "/tmp/foo.txt"})
    client.disconnect("filesystem-server")
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("huanxin.mcp_client")


# ═══════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════


@dataclass
class MCPServerConfig:
    """MCP Server 连接配置。"""

    name: str                                     # 唯一标识
    transport: str = "stdio"                      # stdio / http
    command: str = ""                             # stdio: 启动命令
    args: list[str] = field(default_factory=list) # stdio: 命令行参数
    url: str = ""                                 # http: 远程地址
    env: dict[str, str] = field(default_factory=dict)  # 环境变量
    timeout: float = 30.0                         # 超时（秒）


@dataclass
class MCPTool:
    """MCP Tool 定义。"""

    name: str
    description: str = ""
    parameters_schema: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════


class MCPError(Exception):
    """MCP 协议错误基类。"""
    pass


class MCPConnectionError(MCPError):
    """连接失败 / 断开时抛出。"""
    pass


class MCPToolError(MCPError):
    """工具调用失败时抛出。"""
    pass


class MCPTimeoutError(MCPError):
    """操作超时时抛出。"""
    pass


# ═══════════════════════════════════════════════════════════════════
# MCPClient
# ═══════════════════════════════════════════════════════════════════


class MCPClient:
    """MCP 协议客户端。

    管理多个 MCP Server 连接，通过 JSON-RPC 2.0 协议通信。
    支持 stdio（子进程 stdin/stdout）和 HTTP（POST）两种传输方式。

    >>> client = MCPClient()
    >>> cfg = MCPServerConfig(name="calc", transport="stdio", command="python", args=["calc_server.py"])
    >>> client.connect(cfg)
    >>> client.call_tool("calc", "add", {"a": 1, "b": 2})
    """

    _DEFAULT_TIMEOUT = 30.0

    def __init__(self) -> None:
        # name → {config, process, transport_info}
        self._servers: Dict[str, dict] = {}
        self._lock = threading.RLock()
        # 请求 ID 计数器
        self._next_id: int = 0

    # ── connect ─────────────────────────────────────────────────

    def connect(self, config: MCPServerConfig) -> bool:
        """连接到 MCP Server。

        Args:
            config: 服务器配置。

        Returns:
            连接成功返回 True。

        Raises:
            MCPConnectionError: 连接失败。
        """
        with self._lock:
            if config.name in self._servers:
                raise MCPConnectionError(
                    f"Server '{config.name}' is already connected"
                )

            transport_info: dict = {}

            if config.transport == "stdio":
                transport_info = self._connect_stdio(config)
            elif config.transport == "http":
                transport_info = self._connect_http(config)
            else:
                raise MCPConnectionError(
                    f"Unknown transport '{config.transport}'. "
                    f"Supported: stdio, http"
                )

            self._servers[config.name] = {
                "config": config,
                "process": transport_info.get("process"),
                "transport_info": transport_info,
            }

            logger.info("MCP client connected to '%s' via %s",
                        config.name, config.transport)
            return True

    def _connect_stdio(self, config: MCPServerConfig) -> dict:
        """通过子进程 stdin/stdout 连接。"""
        cmd = [config.command] + list(config.args)
        env = None
        if config.env:
            import os
            env = dict(os.environ)
            env.update(config.env)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
        except FileNotFoundError as e:
            raise MCPConnectionError(
                f"Command not found for '{config.name}': {config.command}"
            ) from e
        except Exception as e:
            raise MCPConnectionError(
                f"Failed to start stdio server '{config.name}': {e}"
            ) from e

        # MCP 握手: initialize
        try:
            self._rpc_call(config.name, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "huanxin-ai",
                    "version": "1.0",
                },
            }, timeout=config.timeout, _proc=proc)
        except MCPError as e:
            proc.terminate()
            proc.wait()
            raise MCPConnectionError(
                f"Initialize handshake failed for '{config.name}': {e}"
            ) from e

        return {"process": proc}

    def _connect_http(self, config: MCPServerConfig) -> dict:
        """通过 HTTP 连接并进行握手。"""
        if not config.url:
            raise MCPConnectionError(
                f"URL required for HTTP transport on '{config.name}'"
            )

        try:
            self._rpc_call(config.name, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "huanxin-ai",
                    "version": "1.0",
                },
            }, timeout=config.timeout)
        except MCPError as e:
            raise MCPConnectionError(
                f"Initialize handshake failed for '{config.name}': {e}"
            ) from e

        return {}

    # ── disconnect ──────────────────────────────────────────────

    def disconnect(self, server_name: str) -> None:
        """断开与 MCP Server 的连接。"""
        with self._lock:
            if server_name not in self._servers:
                raise MCPConnectionError(
                    f"Server '{server_name}' is not connected"
                )

            entry = self._servers[server_name]
            proc = entry.get("process")
            if proc is not None:
                try:
                    proc.stdin.close()
                    proc.stdout.close()
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                except Exception:
                    pass

            del self._servers[server_name]
            logger.info("MCP client disconnected from '%s'", server_name)

    # ── list_tools ──────────────────────────────────────────────

    def list_tools(self, server_name: str) -> list[MCPTool]:
        """获取指定 Server 的工具列表。

        Raises:
            MCPConnectionError: Server 未连接。
            MCPToolError: 获取工具列表失败。
        """
        result = self._rpc_call(server_name, "tools/list", {})

        tools_raw = result.get("tools", [])
        tools = []
        for t in tools_raw:
            tools.append(MCPTool(
                name=t.get("name", ""),
                description=t.get("description", ""),
                parameters_schema=t.get("inputSchema", {}),
            ))
        return tools

    # ── call_tool ───────────────────────────────────────────────

    def call_tool(
        self, server_name: str, tool_name: str, arguments: dict,
    ) -> dict:
        """调用指定 Server 的工具。

        Args:
            server_name: MCP Server 名称。
            tool_name: 工具名称。
            arguments: 工具参数。

        Returns:
            工具执行结果（dict）。

        Raises:
            MCPConnectionError: Server 未连接。
            MCPToolError: 工具执行失败。
        """
        result = self._rpc_call(server_name, "tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })

        # MCP 返回格式: {content: [...], isError: bool}
        content = result.get("content", [])
        is_error = result.get("isError", False)

        if is_error:
            error_text = ""
            if content and len(content) > 0:
                error_text = content[0].get("text", "")
            raise MCPToolError(
                f"Tool '{tool_name}' on '{server_name}' returned error: "
                f"{error_text}"
            )

        # 提取文本内容
        texts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
            elif isinstance(item, dict) and "data" in item:
                texts.append(json.dumps(item["data"]))
            else:
                texts.append(str(item))

        return {
            "result": "\n".join(texts) if texts else "",
            "raw": result,
        }

    # ── list_servers ────────────────────────────────────────────

    def list_servers(self) -> list[str]:
        """列出所有已连接的 Server 名称。"""
        return list(self._servers.keys())

    # ── shutdown ────────────────────────────────────────────────

    def shutdown(self) -> None:
        """关闭所有连接（优雅停止）。"""
        with self._lock:
            names = list(self._servers.keys())
        for name in names:
            try:
                self.disconnect(name)
            except Exception:
                logger.warning("Error disconnecting '%s' during shutdown", name)

    # ── JSON-RPC 2.0 内部实现 ──────────────────────────────────

    def _rpc_call(
        self,
        server_name: str,
        method: str,
        params: dict,
        timeout: Optional[float] = None,
        _proc: Optional[subprocess.Popen] = None,
    ) -> dict:
        """发送 JSON-RPC 2.0 请求并返回结果。"""
        with self._lock:
            if server_name not in self._servers and _proc is None:
                raise MCPConnectionError(
                    f"Server '{server_name}' is not connected"
                )

            entry = self._servers.get(server_name, {})
            config = entry.get("config")
            proc = _proc or entry.get("process")
            transport = "stdio" if proc else "http"

            if timeout is None:
                timeout = (
                    config.timeout
                    if config
                    else self._DEFAULT_TIMEOUT
                )

        request_id = self._next_request_id()
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        request_str = json.dumps(request, ensure_ascii=False) + "\n"

        if transport == "stdio":
            return self._rpc_stdio(request_str, request_id, proc, timeout, server_name)
        else:
            url = config.url if config else ""
            return self._rpc_http(request_str, request_id, url, timeout, server_name)

    def _rpc_stdio(
        self,
        request_str: str,
        request_id: int,
        proc,
        timeout: float,
        server_name: str,
    ) -> dict:
        """通过子进程 stdin/stdout 发送 JSON-RPC 请求。"""
        try:
            proc.stdin.write(request_str)
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPConnectionError(
                f"Broken pipe to '{server_name}': {e}"
            ) from e

        line = None
        deadline = time.time() + timeout

        while time.time() < deadline:
            # 非阻塞读取一行
            try:
                line = proc.stdout.readline()
                if line:
                    break
            except Exception:
                pass

            # 检查进程是否已退出
            poll = proc.poll()
            if poll is not None:
                stderr_text = ""
                try:
                    stderr_text = proc.stderr.read()
                except Exception:
                    pass
                raise MCPConnectionError(
                    f"Server '{server_name}' exited with code {poll}. "
                    f"stderr: {stderr_text[:500]}"
                )
            time.sleep(0.01)

        if not line:
            proc.terminate()
            proc.wait()
            raise MCPTimeoutError(
                f"Timeout ({timeout}s) waiting for response from "
                f"'{server_name}'"
            )

        return self._parse_response(line, request_id, server_name)

    def _rpc_http(
        self,
        request_str: str,
        request_id: int,
        url: str,
        timeout: float,
        server_name: str,
    ) -> dict:
        """通过 HTTP POST 发送 JSON-RPC 请求。"""
        data = request_str.encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
        except HTTPError as e:
            raise MCPConnectionError(
                f"HTTP {e.code} from '{server_name}': {e.reason}"
            ) from e
        except URLError as e:
            raise MCPConnectionError(
                f"Connection failed to '{server_name}' ({url}): {e.reason}"
            ) from e

        return self._parse_response(body, request_id, server_name)

    def _parse_response(
        self, raw: str, request_id: int, server_name: str,
    ) -> dict:
        """解析 JSON-RPC 2.0 响应。"""
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as e:
            raise MCPError(
                f"Invalid JSON from '{server_name}': {e}"
            ) from e

        if "error" in response:
            err = response["error"]
            raise MCPError(
                f"RPC error [{err.get('code', -1)}] from "
                f"'{server_name}': {err.get('message', '')}"
            )
        if "result" not in response:
            raise MCPError(
                f"Invalid response from '{server_name}': "
                f"missing 'result' field"
            )

        return response["result"]

    def _next_request_id(self) -> int:
        """生成单调递增请求 ID。"""
        with self._lock:
            self._next_id += 1
            return self._next_id


# ═══════════════════════════════════════════════════════════════════
# SimpleMCPClient — 同步便捷封装
# ═══════════════════════════════════════════════════════════════════

# SimpleMCPClient 仅为便捷别名，等同于 MCPClient
SimpleMCPClient = MCPClient
