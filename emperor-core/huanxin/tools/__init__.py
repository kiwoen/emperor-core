"""
HUANXIN Tools — Tool execution infrastructure.

Provides:
- ToolDef / ToolResult / @tool — Function Calling base classes and decorator.
- ToolRegistry — singleton registry with schema generation.
- builtin — twelve built-in tools (datetime, math, random, text, etc.).
- ToolCallValidator / safe_execute — Pydantic-based validation + retry.
- AuditTrail — SQLite-based audit logging with trace replay.
"""

from huanxin.tools.base import ToolDef, ToolResult, tool
from huanxin.tools.registry import ToolRegistry, get_registry, reset_registry

from huanxin.tools.validator import (
    ToolCallValidator,
    ToolCallLog,
    ValidationError,
    safe_execute,
    global_validator,
)

from huanxin.tools.audit_trail import (
    AuditTrail,
    AuditRecord,
    AuditReplayer,
)

__all__ = [
    # Base
    "ToolDef",
    "ToolResult",
    "tool",
    # Registry
    "ToolRegistry",
    "get_registry",
    "reset_registry",
    # Validator
    "ToolCallValidator",
    "ToolCallLog",
    "ValidationError",
    "safe_execute",
    "global_validator",
    # Audit
    "AuditTrail",
    "AuditRecord",
    "AuditReplayer",
]
