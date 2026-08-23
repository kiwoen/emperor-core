"""
MCPServer — MCP protocol server exposing emperor-core's 12 built-in capabilities.

Supports stdio and SSE transports via the ``mcp`` package (FastMCP), with
dynamic tool registration, call logging, and statistics.

Usage:
    from jarvis.mcp.server import MCPServer

    server = MCPServer(name="emperor-core")
    server.run(transport="stdio")        # start as stdio server
    server.run(transport="sse", port=8000)  # start as SSE server
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

from jarvis.mcp.tool_registry import ToolRegistry, ToolDef

logger = logging.getLogger("jarvis.mcp.server")


# ═══════════════════════════════════════════════════════════════════
# Built-in tool handlers — one per capability
# ═══════════════════════════════════════════════════════════════════


def _tool_datetime() -> str:
    """Get current date, time, timezone, and weekday."""
    now = dt.datetime.now().astimezone()
    utc_offset = now.utcoffset()
    offset_hours = int(utc_offset.total_seconds() / 3600) if utc_offset else 0
    offset_str = f"UTC{offset_hours:+d}"
    return f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}  |  星期{['一','二','三','四','五','六','日'][now.weekday()]}  |  {offset_str}"


def _tool_math(expression: str) -> str:
    """Safely evaluate a mathematical expression.

    Supports: +, -, *, /, **, %, //, abs, sqrt, sin, cos, tan, log, log10, pi, e
    """
    allowed_names = {
        "abs": abs, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
        "tan": math.tan, "log": math.log, "log10": math.log10, "log2": math.log2,
        "pi": math.pi, "e": math.e, "ceil": math.ceil, "floor": math.floor,
        "pow": pow, "round": round, "min": min, "max": max,
    }
    compiled = compile(expression, "<math>", "eval")
    for node in compiled.co_names:
        if node not in allowed_names:
            raise ValueError(f"Forbidden name in expression: {node}")
    result = eval(compiled, {"__builtins__": {}}, allowed_names)
    return f"{expression} = {result}"


def _tool_random(kind: str = "number", low: float = 0.0, high: float = 1.0, count: int = 1) -> str:
    """Generate random values.

    Args:
        kind:  One of "number", "integer", "dice", "choice".
        low:   Lower bound (or pool start).
        high:  Upper bound (exclusive for number, inclusive for integer).
        count: How many values to generate.
    """
    if kind == "dice":
        faces = int(high) if high > 0 else 6
        rolls = [random.randint(1, faces) for _ in range(max(1, count))]
        return f"掷 {count}d{faces}: {rolls}  sum={sum(rolls)}"
    elif kind == "integer":
        vals = [random.randint(int(low), int(high)) for _ in range(max(1, count))]
        return f"随机整数 [{int(low)}, {int(high)}]: {vals}"
    elif kind == "choice":
        return f"随机选择: {random.random()}"  # simplified
    else:
        vals = [round(random.uniform(low, high), 6) for _ in range(max(1, count))]
        return f"随机浮点数 [{low}, {high}): {vals}"


def _tool_text(operation: str = "stats", text: str = "") -> str:
    """Text analysis and transformation.

    Operations: stats, reverse, upper, lower, count_chars, count_words
    """
    if operation == "stats":
        chars = len(text)
        words = len(text.split()) if text.strip() else 0
        lines = text.count("\n") + 1 if text else 0
        return f"字符数: {chars}  单词数: {words}  行数: {lines}"
    elif operation == "reverse":
        return text[::-1]
    elif operation == "upper":
        return text.upper()
    elif operation == "lower":
        return text.lower()
    elif operation == "count_chars":
        return f"字符数: {len(text)}"
    elif operation == "count_words":
        return f"单词数: {len(text.split()) if text.strip() else 0}"
    else:
        raise ValueError(f"Unknown text operation: {operation}")


def _tool_file_info(path: str) -> str:
    """Get file metadata: size, modification time, line count (text files), extension."""
    p = Path(path)
    if not p.exists():
        return f"错误: 文件不存在 — {path}"
    stat = p.stat()
    size = stat.st_size
    mtime = dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    ext = p.suffix or "(无)"
    lines = ""
    if p.is_file() and p.suffix in {".txt", ".py", ".md", ".json", ".yaml", ".yml", ".csv", ".log", ".ini", ".toml"}:
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                lines = f"行数: {sum(1 for _ in f)}  "
        except Exception:
            pass
    return f"文件: {p.name}  大小: {size:,} bytes  修改: {mtime}  {lines}扩展名: {ext}"


def _tool_hash(algorithm: str = "sha256", text: str = "") -> str:
    """Compute hash digest of *text*.

    Supported algorithms: md5, sha1, sha256
    """
    algo = algorithm.lower()
    if algo == "md5":
        h = hashlib.md5(text.encode("utf-8")).hexdigest()
    elif algo == "sha1":
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    elif algo == "sha256":
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")
    return f"{algo.upper()}: {h}"


def _tool_json_tool(operation: str = "format", json_text: str = "") -> str:
    """JSON formatting / validation / compression."""
    if operation == "format":
        parsed = json.loads(json_text)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    elif operation == "validate":
        try:
            json.loads(json_text)
            return "✓ 有效的 JSON"
        except json.JSONDecodeError as e:
            return f"✗ 无效 JSON: {e}"
    elif operation == "compress":
        parsed = json.loads(json_text)
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    else:
        raise ValueError(f"Unknown JSON operation: {operation}")


def _tool_uuid_gen(count: int = 1) -> str:
    """Generate UUID4 identifiers."""
    if count <= 0:
        count = 1
    ids = [str(uuid.uuid4()) for _ in range(count)]
    return "\n".join(ids)


def _tool_weather(city: str = "Beijing") -> str:
    """Query current weather for a city using wttr.in."""
    encoded = urllib.parse.quote(city)
    url = f"https://wttr.in/{encoded}?format=j1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EmperorCore/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        current = data.get("current_condition", [{}])[0]
        weather_desc = current.get("weatherDesc", [{}])[0].get("value", "N/A")
        temp_c = current.get("temp_C", "N/A")
        humidity = current.get("humidity", "N/A")
        wind_speed = current.get("windspeedKmph", "N/A")
        return (
            f"城市: {city}  温度: {temp_c}°C  天气: {weather_desc}  "
            f"湿度: {humidity}%  风速: {wind_speed} km/h"
        )
    except Exception as exc:
        return f"天气查询失败 ({city}): {exc}"


def _tool_news(topic: str = "technology", count: int = 5) -> str:
    """Query latest news via Google News RSS."""
    encoded = urllib.parse.quote(topic)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EmperorCore/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            rss_data = resp.read().decode()
    except Exception as exc:
        return f"新闻查询失败 ({topic}): {exc}"

    items = []
    item_pattern = re.compile(r"<item>(.*?)</item>", re.DOTALL)
    for match in item_pattern.finditer(rss_data):
        block = match.group(1)
        title_match = re.search(r"<title>(.*?)</title>", block, re.DOTALL)
        source_match = re.search(r"<source[^>]*>(.*?)</source>", block)
        if title_match:
            title = title_match.group(1).strip()
            title = title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            source = source_match.group(1).strip() if source_match else "Unknown"
            items.append({"title": title, "source": source})

    lines = [f"{topic.title()} 新闻"]
    for i, item in enumerate(items[:count], 1):
        lines.append(f"{i}. {item['title'][:80]} — {item['source']}")
    return "\n".join(lines)


def _tool_web_search(query: str = "", max_results: int = 5) -> str:
    """Search the web using DuckDuckGo HTML."""
    if not query.strip():
        return "错误: 搜索关键词不能为空"
    encoded = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EmperorCore/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode()
    except Exception as exc:
        return f"搜索失败 ({query}): {exc}"

    results = []
    link_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)
    for i, (href, title) in enumerate(links[:max_results]):
        title_clean = re.sub(r"<[^>]+>", "", title).strip()
        snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
        results.append(f"{i + 1}. {title_clean}\n   {href}\n   {snippet}")

    return f"搜索: {query}\n\n" + "\n\n".join(results) if results else f"搜索 '{query}' 未找到结果"


def _tool_web_fetch(url: str = "") -> str:
    """Fetch and return text content from a URL."""
    if not url.strip():
        return "错误: URL 不能为空"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EmperorCore/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode(errors="ignore")
            content_type = resp.headers.get("Content-Type", "")
    except Exception as exc:
        return f"抓取失败 ({url}): {exc}"

    # Strip HTML tags for plain-text preview
    stripped = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
    stripped = re.sub(r"<script[^>]*>.*?</script>", "", stripped, flags=re.DOTALL)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()

    preview = stripped[:1500]
    if len(stripped) > 1500:
        preview += f"\n...(截断，共 {len(stripped):,} 字符)"
    return f"URL: {url}\nContent-Type: {content_type}\n\n{preview}"


# ═══════════════════════════════════════════════════════════════════
# Built-in tool metadata
# ═══════════════════════════════════════════════════════════════════

_BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "func": _tool_datetime,
        "name": "datetime",
        "description": "获取当前日期、时间、时区、星期等信息",
        "parameters_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "group": "general",
        "tags": ["general", "time"],
    },
    {
        "func": _tool_math,
        "name": "math",
        "description": "安全计算数学表达式（加减乘除、幂、取模、三角函数等）",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "数学表达式，如 '(2 + 3) * 4'",
                },
            },
            "required": ["expression"],
        },
        "group": "math",
        "tags": ["math", "science", "calculation"],
    },
    {
        "func": _tool_random,
        "name": "random",
        "description": "生成随机数、掷骰子、随机选择",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "类型: number / integer / dice / choice",
                    "default": "number",
                },
                "low": {
                    "type": "number",
                    "description": "下界",
                    "default": 0.0,
                },
                "high": {
                    "type": "number",
                    "description": "上界",
                    "default": 1.0,
                },
                "count": {
                    "type": "integer",
                    "description": "生成数量",
                    "default": 1,
                },
            },
        },
        "group": "general",
        "tags": ["general", "random"],
    },
    {
        "func": _tool_text,
        "name": "text",
        "description": "文本统计、反转、大小写转换",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "操作: stats / reverse / upper / lower / count_chars / count_words",
                    "default": "stats",
                },
                "text": {"type": "string", "description": "输入文本"},
            },
            "required": ["operation", "text"],
        },
        "group": "text",
        "tags": ["text", "general", "code"],
    },
    {
        "func": _tool_file_info,
        "name": "file_info",
        "description": "获取文件大小、修改时间、行数等信息",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件的绝对路径",
                },
            },
            "required": ["path"],
        },
        "group": "file",
        "tags": ["file", "data"],
    },
    {
        "func": _tool_hash,
        "name": "hash",
        "description": "计算字符串的 MD5 / SHA1 / SHA256 哈希摘要",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "algorithm": {
                    "type": "string",
                    "description": "哈希算法: md5 / sha1 / sha256",
                    "default": "sha256",
                },
                "text": {"type": "string", "description": "要计算哈希的字符串"},
            },
            "required": ["text"],
        },
        "group": "data",
        "tags": ["data", "crypto", "code"],
    },
    {
        "func": _tool_json_tool,
        "name": "json_tool",
        "description": "JSON 格式化美化 / 校验 / 压缩",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "操作: format / validate / compress",
                    "default": "format",
                },
                "json_text": {"type": "string", "description": "JSON 字符串"},
            },
            "required": ["json_text"],
        },
        "group": "data",
        "tags": ["data", "code", "json"],
    },
    {
        "func": _tool_uuid_gen,
        "name": "uuid_gen",
        "description": "生成 UUID4 唯一标识符",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "生成数量",
                    "default": 1,
                },
            },
        },
        "group": "general",
        "tags": ["general", "code"],
    },
    {
        "func": _tool_weather,
        "name": "weather",
        "description": "查询城市天气（温度/湿度/风力等）",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名，如 Beijing / Shanghai",
                    "default": "Beijing",
                },
            },
        },
        "group": "network",
        "tags": ["network", "general"],
    },
    {
        "func": _tool_news,
        "name": "news",
        "description": "查询最新新闻资讯，支持中英文关键词",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "新闻主题关键词",
                    "default": "technology",
                },
                "count": {
                    "type": "integer",
                    "description": "返回条数",
                    "default": 5,
                },
            },
        },
        "group": "network",
        "tags": ["network", "general"],
    },
    {
        "func": _tool_web_search,
        "name": "web_search",
        "description": "搜索互联网信息（通过 DuckDuckGo）",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        "group": "network",
        "tags": ["network", "search", "data"],
    },
    {
        "func": _tool_web_fetch,
        "name": "web_fetch",
        "description": "抓取指定网页的内容",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "网页 URL",
                },
            },
            "required": ["url"],
        },
        "group": "network",
        "tags": ["network", "data", "code"],
    },
]


# ═══════════════════════════════════════════════════════════════════
# MCPServer
# ═══════════════════════════════════════════════════════════════════


class MCPServer:
    """MCP Server wrapping emperor-core's 12 built-in capabilities.

    Uses the ``mcp`` package's FastMCP (``mcp>=1.0.0``) under the hood when
    available; falls back to a manual MCP protocol implementation otherwise.

    Transport support:
        - ``stdio`` — stdin/stdout JSON-RPC (default)
        - ``sse``   — HTTP SSE endpoint

    Basic usage::

        from jarvis.mcp.server import MCPServer

        server = MCPServer(name="emperor-core")
        # server.run(transport="stdio")
        # server.run(transport="sse", port=8000)

    Dynamic registration::

        def my_tool(x: int) -> str:
            return f"Result: {x * 2}"

        server.register_tool(my_tool, "double", "Double a number",
                             parameters_schema={"type": "object", "properties": {
                                 "x": {"type": "integer"}}})
        server.unregister_tool("double")
    """

    def __init__(self, name: str = "emperor-core") -> None:
        self.name: str = name
        self._registry: ToolRegistry = ToolRegistry()
        self._mcp_instance: Any = None  # FastMCP instance, set by _init_mcp()
        self._transport: Optional[str] = None

        # Register the 12 built-in capabilities
        for meta in _BUILTIN_TOOLS:
            self._registry.register(
                func=meta["func"],
                name=meta["name"],
                description=meta["description"],
                parameters_schema=meta.get("parameters_schema", {}),
                group=meta.get("group", "default"),
                tags=meta.get("tags", []),
            )
        logger.info(
            "[MCPServer] Initialized '%s' with %d built-in tools",
            self.name, self._registry.tool_count(),
        )

    # ── Dynamic registration ─────────────────────────────────────────

    def register_tool(
        self,
        func: callable,
        name: str,
        description: str,
        parameters_schema: Optional[dict] = None,
        group: str = "default",
        tags: Optional[list[str]] = None,
    ) -> ToolDef:
        """Register a new tool dynamically.

        Also registers with the underlying FastMCP instance if running.
        """
        tool = self._registry.register(
            func=func,
            name=name,
            description=description,
            parameters_schema=parameters_schema,
            group=group,
            tags=tags,
        )
        if self._mcp_instance is not None:
            self._register_with_fastmcp(tool)
        return tool

    def unregister_tool(self, name: str) -> bool:
        """Unregister a tool by name.

        Also removes it from the underlying FastMCP instance if running.
        """
        # FastMCP doesn't support dynamic tool removal natively;
        # we remove from our registry and the tool becomes unavailable.
        return self._registry.unregister(name)

    # ── Discovery ────────────────────────────────────────────────────

    def list_tools(self, group: Optional[str] = None) -> list[ToolDef]:
        """List registered tools, optionally filtered by group."""
        return self._registry.list_tools(group=group)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool by name with keyword arguments."""
        return self._registry.call_tool(name, arguments)

    def get_stats(self) -> dict:
        """Return aggregate call statistics."""
        return self._registry.get_stats()

    # ── Server lifecycle ─────────────────────────────────────────────

    def run(self, transport: str = "stdio", **kwargs: Any) -> None:
        """Start the MCP server with the specified transport.

        Args:
            transport: ``"stdio"`` or ``"sse"``.
            **kwargs: Additional options forwarded to FastMCP.run()
                      (e.g. ``port``, ``host`` for SSE).
        """
        self._transport = transport
        try:
            self._run_fastmcp(transport, **kwargs)
        except ImportError:
            logger.warning("[MCPServer] mcp package not available; using manual protocol")
            self._run_manual(transport, **kwargs)

    def _init_mcp(self) -> Any:
        """Import and initialise FastMCP."""
        from mcp.server.fastmcp import FastMCP
        self._mcp_instance = FastMCP(self.name)
        # Register all existing tools
        for tool in self._registry.list_tools():
            self._register_with_fastmcp(tool)
        return self._mcp_instance

    def _register_with_fastmcp(self, tool: ToolDef) -> None:
        """Register a ToolDef with the FastMCP instance."""
        if self._mcp_instance is None:
            return
        # Use the FastMCP tool decorator programmatically
        mcp = self._mcp_instance
        mcp.add_tool(tool.func, name=tool.name, description=tool.description)

    def _run_fastmcp(self, transport: str, **kwargs: Any) -> None:
        """Start server using FastMCP."""
        mcp = self._init_mcp()
        if transport == "sse":
            port = kwargs.pop("port", 8000)
            host = kwargs.pop("host", "0.0.0.0")
            logger.info("[MCPServer] Starting FastMCP SSE server on %s:%d", host, port)
            mcp.run(transport="sse", host=host, port=port, **kwargs)
        else:
            logger.info("[MCPServer] Starting FastMCP stdio server")
            mcp.run(transport="stdio", **kwargs)

    def _run_manual(self, transport: str, **kwargs: Any) -> None:
        """Fallback: manual MCP JSON-RPC implementation.

        Implements a minimal subset of the MCP protocol:
          - initialize
          - tools/list
          - tools/call
        """
        if transport == "sse":
            logger.error("[MCPServer] Manual SSE transport not implemented — install `mcp` package")
            raise RuntimeError("Manual SSE transport requires the `mcp` package. Install with: pip install mcp>=1.0.0")

        logger.info("[MCPServer] Starting manual MCP stdio server")

        def _send_response(request_id: Any, result: Any) -> None:
            payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})
            print(payload, flush=True)

        def _send_error(request_id: Any, code: int, message: str) -> None:
            payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})
            print(payload, flush=True)

        for line in os.sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                _send_error(None, -32700, "Parse error")
                continue

            method = msg.get("method", "")
            request_id = msg.get("id")

            if method == "initialize":
                _send_response(request_id, {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": self.name, "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                })
            elif method == "tools/list":
                tools = []
                for tool in self._registry.list_tools():
                    tools.append({
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.parameters_schema,
                    })
                _send_response(request_id, {"tools": tools})
            elif method == "tools/call":
                params = msg.get("params", {})
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                try:
                    result = self._registry.call_tool(tool_name, arguments)
                    _send_response(request_id, {
                        "content": [{"type": "text", "text": str(result)}],
                    })
                except KeyError:
                    _send_error(request_id, -32601, f"Tool not found: {tool_name}")
                except Exception as exc:
                    _send_error(request_id, -32603, str(exc))
            elif method == "notifications/initialized":
                pass  # ack only, no response
            else:
                _send_error(request_id, -32601, f"Method not found: {method}")
