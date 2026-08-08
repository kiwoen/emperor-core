"""
jarvis.mcp — MCP resilience, connection management, tool registry, and server.

Provides:
  - Circuit breaker, exponential backoff retry, timeout management,
    and OAuth token lifecycle for MCP server connections.
  - ToolRegistry: thread-safe tool registration with grouping, tagging, and stats.
  - MCPServer:   MCP protocol server exposing 12 built-in capabilities
                 via stdio / SSE transports.
"""

# ── Resilience & connection management ─────────────────────────────
from jarvis.mcp.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    ResilientMCPClient,
    MCPAuthManager,
    OAuthToken,
    CircuitBreakerConfig,
    RetryConfig,
)

# ── Tool registry ──────────────────────────────────────────────────
from jarvis.mcp.tool_registry import (
    ToolRegistry,
    ToolDef,
)

# ── MCP Server ─────────────────────────────────────────────────────
from jarvis.mcp.server import (
    MCPServer,
)

__all__ = [
    # circuit_breaker
    "CircuitBreaker",
    "CircuitState",
    "ResilientMCPClient",
    "MCPAuthManager",
    "OAuthToken",
    "CircuitBreakerConfig",
    "RetryConfig",
    # tool_registry
    "ToolRegistry",
    "ToolDef",
    # server
    "MCPServer",
]
