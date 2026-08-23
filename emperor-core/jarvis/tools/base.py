"""
JARVIS Tools — Function Calling base classes and decorators.

Provides :class:`ToolDef` for tool metadata, :class:`ToolResult` for
standardised execution results, and the ``@tool`` decorator that
auto-generates ``ToolDef`` from function signature + docstring.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, get_args, get_origin

logger = logging.getLogger("jarvis.tools.base")


# ═══════════════════════════════════════════════════════════════════
# JSON Schema type mapping helpers
# ═══════════════════════════════════════════════════════════════════

_JSON_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _annotation_to_json_schema(ann: Any) -> dict:
    """Convert a Python type annotation to a JSON Schema fragment.

    Handles: str, int, float, bool, list, dict, Optional[X], and
    types with ``__origin__``.
    """
    origin = get_origin(ann)
    args = get_args(ann)

    # Optional[X] → Union[X, None]
    if origin is type(None):  # NoneType
        return {"type": "null"}
    if origin is not None and type(None) in args:
        # Optional[X] — strip None, schema for X
        inner = next((a for a in args if a is not type(None)), str)
        schema = _annotation_to_json_schema(inner)
        if "type" in schema and isinstance(schema["type"], str):
            schema = {**schema, "type": [schema["type"], "null"]}
        return schema

    if origin is list or origin is set:
        item_type = args[0] if args else str
        return {"type": "array", "items": _annotation_to_json_schema(item_type)}
    if origin is dict:
        return {"type": "object"}

    # Plain types
    for py_type, json_type in _JSON_TYPE_MAP.items():
        if ann is py_type:
            return {"type": json_type}

    # Fallback for unknown types
    if isinstance(ann, type):
        return {"type": "string", "description": f"({ann.__name__})"}
    return {"type": "string"}


def _extract_param_schema(func: Callable) -> dict[str, Any]:
    """Build JSON Schema ``properties`` dict from function signature + type hints.

    Returns a dict suitable for ``ToolDef.parameters["properties"]``.
    """
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return {}

    properties: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        ann = param.annotation if param.annotation is not inspect.Parameter.empty else str
        prop = _annotation_to_json_schema(ann)

        # Default value
        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default

        properties[name] = prop
    return properties


def _extract_docstring_description(func: Callable) -> str:
    """Extract the first sentence / paragraph of the function docstring."""
    doc = (func.__doc__ or "").strip()
    if not doc:
        return func.__name__.replace("_", " ").title()

    # Take first non-empty, non-tag line / paragraph
    lines = []
    for line in doc.split("\n"):
        stripped = line.strip()
        if not stripped:
            if lines:
                break
            continue
        # Stop at reST / Google-style tags
        if stripped.startswith(("Args:", "Returns:", "Raises:", "Example", "Usage", ":")):
            break
        lines.append(stripped)
    return " ".join(lines).strip()


# ═══════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ToolResult:
    """Standardised result from a tool execution.

    Attributes:
        success:     Whether the tool executed without error.
        data:        The return value of the tool function (if successful).
        error:       Error message string (if ``success`` is False).
        duration_ms: Wall-clock execution time in milliseconds.
    """

    success: bool
    data: Any = None
    error: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


@dataclass
class ToolDef:
    """Definition of a registered tool.

    Attributes:
        name:        Unique tool identifier.
        description: Human-readable description of what the tool does.
        parameters:  JSON Schema dict for the tool's input parameters
                     (OpenAI Function Calling compatible).
        func:        The underlying callable.
        category:    Logical category (e.g. "utility", "network", "math").
    """

    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    func: Optional[Callable] = None
    category: str = "general"

    def to_openai_schema(self) -> dict:
        """Return an OpenAI Function Calling compatible tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_schema(self) -> dict:
        """Return an Anthropic Tool Use compatible tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.parameters.get("properties", {}),
                "required": self.parameters.get("required", []),
            },
        }


# ═══════════════════════════════════════════════════════════════════
# @tool decorator
# ═══════════════════════════════════════════════════════════════════


class tool:
    """Decorator that wraps a function into a :class:`ToolDef`.

    Automatically extracts:
    - ``name`` from the function name.
    - ``description`` from the first paragraph of the docstring.
    - ``parameters`` JSON Schema from type hints and defaults.

    The wrapped function returns a :class:`ToolResult` (timed execution,
    caught exceptions → error field).

    Usage::

        @tool(category="utility")
        def get_datetime(format: str = "%Y-%m-%d") -> ToolResult:
            '''Return the current date and time.'''
            import datetime
            return datetime.datetime.now().strftime(format)

        # Access auto-generated ToolDef
        tool_def = get_datetime.tool_def
        print(tool_def.to_openai_schema())

        # Execute and get standardised ToolResult
        result: ToolResult = get_datetime(format="%Y-%m-%d")
    """

    def __init__(self, *, name: Optional[str] = None, description: Optional[str] = None,
                 category: str = "general", auto_register: bool = False):
        self._name = name
        self._description = description
        self._category = category
        self._auto_register = auto_register
        self.tool_def: Optional[ToolDef] = None

    def __call__(self, func: Callable) -> Callable:
        name = self._name or func.__name__
        description = self._description or _extract_docstring_description(func)
        properties = _extract_param_schema(func)
        required = [
            k for k, v in properties.items()
            if "default" not in v
        ]

        self.tool_def = ToolDef(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
            func=func,
            category=self._category,
        )

        # Auto-register if requested
        if self._auto_register:
            from jarvis.tools.registry import get_registry

            get_registry().register_tool(self.tool_def)

        def wrapper(*args, **kwargs) -> ToolResult:
            start = time.perf_counter()
            try:
                data = func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                return ToolResult(success=True, data=data, duration_ms=duration_ms)
            except Exception as exc:
                duration_ms = (time.perf_counter() - start) * 1000
                logger.debug("Tool '%s' failed: %s", name, exc)
                return ToolResult(success=False, error=str(exc), duration_ms=duration_ms)

        wrapper.tool_def = self.tool_def  # type: ignore[attr-defined]
        wrapper._is_tool = True  # type: ignore[attr-defined]
        return wrapper
