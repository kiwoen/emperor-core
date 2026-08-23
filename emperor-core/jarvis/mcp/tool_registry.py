"""
Tool Registry — unified tool registration, discovery, and invocation.

Thread-safe registry with grouping, tagging, call statistics, and structured
parameter schema support. Designed as the internal engine for MCPServer.

Usage:
    from jarvis.mcp.tool_registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(my_func, "add", "Add two numbers",
                 parameters_schema={"type": "object", "properties": {...}})
    result = reg.call_tool("add", {"a": 1, "b": 2})
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("jarvis.mcp.tool_registry")


# ═══════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ToolDef:
    """Definition of a registered tool.

    Attributes:
        name:              Unique tool identifier.
        description:       Human-readable description.
        func:              Callable handler.
        parameters_schema: JSON Schema for input parameters.
        group:             Logical grouping (e.g. "network", "math").
        tags:              Arbitrary tags for filtering.
        call_count:        Total number of successful invocations.
        last_called:       Timestamp of most recent call.
        total_call_time:   Cumulative execution time in seconds.
    """

    name: str
    description: str
    func: Callable[..., Any]
    parameters_schema: dict = field(default_factory=dict)
    group: str = "default"
    tags: list[str] = field(default_factory=list)
    call_count: int = 0
    last_called: float = 0.0
    total_call_time: float = 0.0


# ═══════════════════════════════════════════════════════════════════
# ToolRegistry
# ═══════════════════════════════════════════════════════════════════


class ToolRegistry:
    """Thread-safe tool registry with grouping, tagging, and statistics.

    >>> reg = ToolRegistry()
    >>> reg.register(lambda a, b: a + b, "add", "Add two numbers",
    ...              parameters_schema={"type": "object", "properties": {
    ...                  "a": {"type": "number"}, "b": {"type": "number"}}})
    >>> reg.list_tools()
    [ToolDef(name="add", ...)]
    >>> reg.call_tool("add", {"a": 1, "b": 2})
    3
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}
        self._lock: threading.Lock = threading.Lock()

    # ── Registration ─────────────────────────────────────────────────

    def register(
        self,
        func: Callable[..., Any],
        name: str,
        description: str,
        parameters_schema: Optional[dict] = None,
        group: str = "default",
        tags: Optional[list[str]] = None,
    ) -> ToolDef:
        """Register a new tool.

        Args:
            func:              The callable handler.
            name:              Unique tool name.
            description:       Human-readable description.
            parameters_schema: JSON Schema dict describing the arguments.
            group:             Logical group name.
            tags:              Optional tags for filtering.

        Returns:
            The newly created :class:`ToolDef`.

        Raises:
            ValueError: If *name* is already registered.
        """
        if not name or not callable(func):
            raise ValueError("Tool must have a non-empty name and a callable handler")

        with self._lock:
            if name in self._tools:
                raise ValueError(f"Tool '{name}' is already registered")
            tool = ToolDef(
                name=name,
                description=description,
                func=func,
                parameters_schema=parameters_schema or {},
                group=group,
                tags=tags or [],
            )
            self._tools[name] = tool
            logger.info("[ToolRegistry] Registered tool '%s' (group=%s)", name, group)
            return tool

    def unregister(self, name: str) -> bool:
        """Unregister a tool by name.

        Returns:
            ``True`` if the tool was removed, ``False`` if it wasn't found.
        """
        with self._lock:
            if name in self._tools:
                del self._tools[name]
                logger.info("[ToolRegistry] Unregistered tool '%s'", name)
                return True
            logger.warning("[ToolRegistry] Cannot unregister — tool '%s' not found", name)
            return False

    # ── Discovery ────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ToolDef]:
        """Get a tool definition by name, or ``None``."""
        with self._lock:
            return self._tools.get(name)

    def list_tools(self, group: Optional[str] = None) -> list[ToolDef]:
        """List all registered tools, optionally filtered by *group*.

        Returns:
            A shallow copy of matching :class:`ToolDef` objects.
        """
        with self._lock:
            tools = list(self._tools.values())
        if group is not None:
            tools = [t for t in tools if t.group == group]
        return sorted(tools, key=lambda t: t.name)

    def list_groups(self) -> list[str]:
        """Return sorted list of distinct group names."""
        with self._lock:
            groups = {t.group for t in self._tools.values()}
        return sorted(groups)

    def search_by_tag(self, tag: str) -> list[ToolDef]:
        """Return tools that carry the given *tag*."""
        with self._lock:
            return [t for t in self._tools.values() if tag in t.tags]

    def tool_count(self) -> int:
        """Return the number of registered tools."""
        with self._lock:
            return len(self._tools)

    # ── Invocation ───────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute a registered tool.

        Args:
            name:      Tool name.
            arguments: Keyword arguments to pass to the handler.

        Returns:
            Whatever the handler returns.

        Raises:
            KeyError: If *name* is not registered.
            Exception: Re-raises any exception from the handler.
        """
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool '{name}' is not registered")

        start = time.perf_counter()
        try:
            result = tool.func(**arguments)
        except Exception:
            elapsed = time.perf_counter() - start
            with self._lock:
                tool.call_count += 1
                tool.last_called = time.time()
                tool.total_call_time += elapsed
            logger.exception("[ToolRegistry] Tool '%s' raised an exception", name)
            raise
        elapsed = time.perf_counter() - start

        with self._lock:
            tool.call_count += 1
            tool.last_called = time.time()
            tool.total_call_time += elapsed
        logger.debug(
            "[ToolRegistry] Tool '%s' completed in %.3fs (call #%d)",
            name, elapsed, tool.call_count,
        )
        return result

    # ── Statistics ───────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return aggregate call statistics.

        Returns:
            Dict with keys ``tool_count``, ``total_calls``,
            ``total_time``, and per-tool breakdown in ``tools``.
        """
        with self._lock:
            tools_data = {
                t.name: {
                    "group": t.group,
                    "tags": t.tags,
                    "call_count": t.call_count,
                    "last_called": t.last_called,
                    "total_call_time": round(t.total_call_time, 4),
                    "avg_call_time": round(t.total_call_time / t.call_count, 4) if t.call_count else 0,
                }
                for t in self._tools.values()
            }
            total_calls = sum(t.call_count for t in self._tools.values())
            total_time = round(sum(t.total_call_time for t in self._tools.values()), 4)

        return {
            "tool_count": len(tools_data),
            "total_calls": total_calls,
            "total_time": total_time,
            "tools": tools_data,
        }

    def reset_stats(self) -> None:
        """Reset all call statistics to zero."""
        with self._lock:
            for tool in self._tools.values():
                tool.call_count = 0
                tool.last_called = 0.0
                tool.total_call_time = 0.0
        logger.info("[ToolRegistry] Statistics reset")
