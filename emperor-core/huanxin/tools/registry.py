"""
Tool Registry — singleton registry for Function Calling tools.

Provides :class:`ToolRegistry` with register / get / list / schema
generation and tool execution. Uses module-level singleton via
:func:`get_registry`.

Usage::

    from huanxin.tools.registry import get_registry
    from huanxin.tools.base import tool, ToolResult

    @tool(category="utility", auto_register=True)
    def reverse(text: str) -> ToolResult:
        '''Reverse a string.'''
        return text[::-1]

    reg = get_registry()
    print(reg.list_tools(category="utility"))
    print(reg.to_openai_schema())
    result = reg.execute_tool("reverse", {"text": "hello"})
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from huanxin.tools.base import ToolDef, ToolResult

logger = logging.getLogger("huanxin.tools.registry")

# ── Module-level singleton ────────────────────────────────────────────
_registry: Optional[ToolRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ToolRegistry:
    """Return the global (process-level) :class:`ToolRegistry` singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ToolRegistry()
    return _registry


def reset_registry() -> None:
    """Reset the global singleton (mainly for testing)."""
    global _registry
    with _registry_lock:
        _registry = None


# ═══════════════════════════════════════════════════════════════════
# ToolRegistry
# ═══════════════════════════════════════════════════════════════════


class ToolRegistry:
    """Thread-safe singleton tool registry.

    Stores :class:`ToolDef` instances and provides query, schema-generation,
    and execution interfaces.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}
        self._lock = threading.Lock()

    # ── Registration ──────────────────────────────────────────────

    def register_tool(self, tool_def: ToolDef) -> None:
        """Register a :class:`ToolDef`.

        Args:
            tool_def: The tool definition to register.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        with self._lock:
            if tool_def.name in self._tools:
                raise ValueError(f"Tool '{tool_def.name}' is already registered")
            self._tools[tool_def.name] = tool_def
            logger.debug("[ToolRegistry] Registered '%s' (category=%s)", tool_def.name, tool_def.category)

    def unregister_tool(self, name: str) -> bool:
        """Remove a tool by name.

        Returns:
            ``True`` if removed, ``False`` if not found.
        """
        with self._lock:
            if name in self._tools:
                del self._tools[name]
                return True
            return False

    # ── Query ─────────────────────────────────────────────────────

    def get_tool(self, name: str) -> Optional[ToolDef]:
        """Get a tool definition by name, or ``None``."""
        with self._lock:
            return self._tools.get(name)

    def list_tools(self, category: Optional[str] = None) -> list[ToolDef]:
        """List registered tools, optionally filtered by *category*."""
        with self._lock:
            tools = list(self._tools.values())
        if category is not None:
            tools = [t for t in tools if t.category == category]
        return sorted(tools, key=lambda t: t.name)

    def list_categories(self) -> list[str]:
        """Return sorted list of distinct categories."""
        with self._lock:
            cats = {t.category for t in self._tools.values()}
        return sorted(cats)

    def tool_count(self) -> int:
        """Return number of registered tools."""
        with self._lock:
            return len(self._tools)

    # ── Schema generation ─────────────────────────────────────────

    def to_openai_schema(self) -> list[dict]:
        """Generate an OpenAI Function Calling compatible ``tools`` array.

        Returns a list of dicts, each containing ``type`` and ``function`` keys.
        """
        with self._lock:
            return [t.to_openai_schema() for t in self._tools.values()]

    def to_anthropic_schema(self) -> list[dict]:
        """Generate an Anthropic Tool Use compatible ``tools`` array.

        Returns a list of dicts with ``name``, ``description``, ``input_schema``.
        """
        with self._lock:
            return [t.to_anthropic_schema() for t in self._tools.values()]

    # ── Execution ─────────────────────────────────────────────────

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a registered tool by name.

        Args:
            name:      Tool name.
            arguments: Keyword arguments to pass to the tool function.

        Returns:
            A :class:`ToolResult` with ``success``, ``data``, ``error``, ``duration_ms``.

        Raises:
            KeyError: If *name* is not registered.
        """
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            available = sorted(self._tools.keys())
            return ToolResult(
                success=False,
                error=f"Tool '{name}' is not registered. Available: {available}",
                duration_ms=0.0,
            )

        if tool.func is None:
            return ToolResult(
                success=False,
                error=f"Tool '{name}' has no callable function.",
                duration_ms=0.0,
            )

        start = time.perf_counter()
        try:
            data = tool.func(**arguments)
            duration_ms = (time.perf_counter() - start) * 1000
            # If the function already returns a ToolResult, use it directly
            if isinstance(data, ToolResult):
                data.duration_ms = duration_ms
                return data
            return ToolResult(success=True, data=data, duration_ms=duration_ms)
        except TypeError as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.debug("Tool '%s' TypeError: %s", name, exc)
            return ToolResult(success=False, error=str(exc), duration_ms=duration_ms)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception("Tool '%s' execution failed: %s", name, exc)
            return ToolResult(success=False, error=str(exc), duration_ms=duration_ms)
