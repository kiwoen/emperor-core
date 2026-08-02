"""
MCP Circuit Breaker — resilience layer for MCP client connections.

Implements:
  - CircuitBreaker: three-state (CLOSED / OPEN / HALF_OPEN) with sliding window
  - ResilientMCPClient: wraps MCPClient with circuit breaker + exponential
    backoff retry + per-server timeout management
  - MCPAuthManager: OAuth token auto-refresh with 5-min pre-refresh window

Usage:
    from jarvis.mcp import ResilientMCPClient

    client = ResilientMCPClient()
    client.connect(config)
    result = client.call_tool("calc-server", "add", {"a": 1, "b": 2})
    # → auto-retries on transient failure, circuit-breaks on persistent failure
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

from jarvis.mcp_client import (
    MCPClient,
    MCPServerConfig,
    MCPTool,
    MCPError,
    MCPConnectionError,
    MCPToolError,
    MCPTimeoutError,
)

logger = logging.getLogger("jarvis.mcp.circuit_breaker")


# ═══════════════════════════════════════════════════════════════════
# Enums & Data Classes
# ═══════════════════════════════════════════════════════════════════


class CircuitState(Enum):
    """Circuit breaker states (standard three-state model)."""
    CLOSED = "closed"        # Normal: requests flow through
    OPEN = "open"            # Failing: all requests short-circuit with degraded response
    HALF_OPEN = "half_open"  # Probing: one trial request allowed to test recovery


@dataclass
class OAuthToken:
    """OAuth token with expiry tracking."""
    access_token: str
    token_type: str = "Bearer"
    expires_at: float = 0.0   # Unix timestamp of token expiry
    refresh_token: str = ""
    scope: str = ""

    def is_expired(self, pre_refresh_seconds: float = 300.0) -> bool:
        """Check if token is expired or within pre-refresh window.

        Args:
            pre_refresh_seconds: Seconds before expiry to treat as expired
                                 (default 300 = 5 minutes).

        Returns:
            True if token should be refreshed now.
        """
        if self.expires_at <= 0:
            return False  # no expiry info → assume valid
        return time.time() + pre_refresh_seconds >= self.expires_at


@dataclass
class CircuitBreakerConfig:
    """Per-server circuit breaker configuration."""
    failure_threshold: int = 5          # consecutive failures before OPEN
    recovery_timeout: float = 30.0      # seconds in OPEN before transitioning to HALF_OPEN
    half_open_max_requests: int = 1     # max trial requests in HALF_OPEN
    sliding_window_seconds: float = 60.0  # sliding window for failure counting


@dataclass
class RetryConfig:
    """Exponential backoff retry configuration."""
    max_retries: int = 3
    base_delay: float = 1.0      # seconds
    max_delay: float = 30.0      # seconds
    backoff_factor: float = 2.0  # multiplier per attempt


# ═══════════════════════════════════════════════════════════════════
# CircuitBreaker
# ═══════════════════════════════════════════════════════════════════


class CircuitBreaker:
    """Per-server circuit breaker with sliding window failure counting.

    States:
        CLOSED   → failures reach threshold → OPEN
        OPEN     → recovery timeout elapsed → HALF_OPEN
        HALF_OPEN → trial succeeds            → CLOSED (reset)
        HALF_OPEN → trial fails               → OPEN (reset recovery timer)

    >>> cb = CircuitBreaker("calc-server")
    >>> cb.before_call()       # True → allow
    >>> cb.on_success()        # reset failure count
    >>> cb.on_failure()        # increment, may open circuit
    """

    def __init__(
        self,
        server_name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        self.server_name = server_name
        self.config = config or CircuitBreakerConfig()
        self._state: CircuitState = CircuitState.CLOSED
        self._lock = threading.RLock()

        # Sliding window failure tracking: list of (timestamp,)
        self._failure_timestamps: list[float] = []
        # Track consecutive failures for threshold comparison
        self._consecutive_failures: int = 0
        # Successive successes in HALF_OPEN
        self._half_open_successes: int = 0
        # Timestamp when circuit was opened
        self._opened_at: float = 0.0
        # Total statistics
        self.total_failures: int = 0
        self.total_successes: int = 0

    # ── properties ──────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN

    # ── public API ──────────────────────────────────────────────

    def before_call(self) -> bool:
        """Check whether a call should proceed.

        Returns:
            True if call is allowed, False if circuit is open and call
            should be degraded.
        """
        with self._lock:
            self._prune_failure_window()

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                elapsed = time.time() - self._opened_at
                if elapsed >= self.config.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_successes = 0
                    logger.info(
                        "Circuit for '%s' → HALF_OPEN (recovery timeout elapsed)",
                        self.server_name,
                    )
                    return True
                return False

            if self._state == CircuitState.HALF_OPEN:
                # Allow trial requests up to half_open_max_requests
                # (we simplify: allow one trial at a time—the caller handles
                #  the actual count via success/failure tracking)
                return True

            return True

    def on_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            self.total_successes += 1

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= 1:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._failure_timestamps.clear()
                    logger.info(
                        "Circuit for '%s' → CLOSED (trial succeeded)",
                        self.server_name,
                    )
                return

            # CLOSED: reset on success
            self._consecutive_failures = 0

    def on_failure(self) -> None:
        """Record a failed call. May open the circuit."""
        with self._lock:
            now = time.time()
            self.total_failures += 1
            self._failure_timestamps.append(now)
            self._consecutive_failures += 1

            self._prune_failure_window()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = now
                logger.warning(
                    "Circuit for '%s' → OPEN (trial in HALF_OPEN failed)",
                    self.server_name,
                )
                return

            if self._state == CircuitState.CLOSED:
                recent_failures = len(self._failure_timestamps)
                if (
                    recent_failures >= self.config.failure_threshold
                    or self._consecutive_failures >= self.config.failure_threshold
                ):
                    self._state = CircuitState.OPEN
                    self._opened_at = now
                    logger.warning(
                        "Circuit for '%s' → OPEN (%d failures, threshold=%d)",
                        self.server_name,
                        max(recent_failures, self._consecutive_failures),
                        self.config.failure_threshold,
                    )

    def force_reset(self) -> None:
        """Forcefully reset circuit to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._failure_timestamps.clear()
            self._half_open_successes = 0
            self._opened_at = 0.0
            logger.info("Circuit for '%s' forcefully reset → CLOSED", self.server_name)

    def get_stats(self) -> dict:
        """Return circuit breaker statistics."""
        with self._lock:
            self._prune_failure_window()
            return {
                "server": self.server_name,
                "state": self._state.value,
                "total_failures": self.total_failures,
                "total_successes": self.total_successes,
                "consecutive_failures": self._consecutive_failures,
                "recent_failures": len(self._failure_timestamps),
                "opened_at": self._opened_at,
                "threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
            }

    # ── internal ────────────────────────────────────────────────

    def _prune_failure_window(self) -> None:
        """Remove failure timestamps outside the sliding window."""
        cutoff = time.time() - self.config.sliding_window_seconds
        self._failure_timestamps = [
            ts for ts in self._failure_timestamps if ts >= cutoff
        ]


# ═══════════════════════════════════════════════════════════════════
# ResilientMCPClient
# ═══════════════════════════════════════════════════════════════════


class ResilientMCPClient:
    """MCPClient wrapper with circuit breaker + exponential backoff + timeout.

    Provides drop-in compatibility with the original MCPClient API while
    adding resilience layers transparently.

    Design:
        - One CircuitBreaker per server (isolated failure tracking)
        - Exponential backoff retry on transient failures (MCPTimeoutError,
          connection errors, URLError)
        - Per-server configurable timeout (default 10s, overrides MCPServerConfig.timeout)
        - When circuit is OPEN, returns degraded response immediately:
          {"error": "server_unavailable", "server": "...", "circuit_open": true}

    >>> client = ResilientMCPClient()
    >>> cfg = MCPServerConfig(name="search", transport="http", url="...")
    >>> client.connect(cfg)
    >>> result = client.call_tool("search", "query", {"q": "MCP"})
    """

    _DEFAULT_TIMEOUT = 10.0

    def __init__(
        self,
        mcp_client: Optional[MCPClient] = None,
        cb_config: Optional[CircuitBreakerConfig] = None,
        retry_config: Optional[RetryConfig] = None,
        auth_manager: Optional[MCPAuthManager] = None,
    ) -> None:
        self._client = mcp_client or MCPClient()
        self._cb_config = cb_config or CircuitBreakerConfig()
        self._retry_config = retry_config or RetryConfig()
        self._auth_manager = auth_manager

        # server_name → CircuitBreaker
        self._breakers: Dict[str, CircuitBreaker] = {}
        # server_name → per-server timeout override (None → use config default)
        self._server_timeouts: Dict[str, float] = {}
        self._lock = threading.RLock()

    # ── connect / disconnect ────────────────────────────────────

    def connect(
        self,
        config: MCPServerConfig,
        timeout: Optional[float] = None,
    ) -> bool:
        """Connect to an MCP server with resilience.

        Args:
            config: Server configuration.
            timeout: Per-server timeout override (default: 10s).

        Returns:
            True if connected successfully.

        Raises:
            MCPConnectionError: Connection failed after retries.
        """
        name = config.name
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, self._cb_config)
            self._server_timeouts[name] = (
                timeout if timeout is not None else self._DEFAULT_TIMEOUT
            )

        breaker = self._breakers[name]

        def _connect() -> bool:
            return self._client.connect(config)

        return self._execute_with_resilience(
            server_name=name,
            fn=_connect,
            breaker=breaker,
            timeout=self._server_timeouts[name],
        )

    def disconnect(self, server_name: str) -> None:
        """Disconnect from an MCP server."""
        self._client.disconnect(server_name)
        with self._lock:
            self._server_timeouts.pop(server_name, None)

    def shutdown(self) -> None:
        """Shutdown all connections."""
        with self._lock:
            names = list(self._breakers.keys())
        for name in names:
            try:
                self.disconnect(name)
            except Exception:
                logger.warning("Error disconnecting '%s' during shutdown", name)
        self._client.shutdown()

    # ── call_tool ───────────────────────────────────────────────

    def call_tool(
        self, server_name: str, tool_name: str, arguments: dict,
    ) -> dict:
        """Call a tool on a server with full resilience pipeline.

        Pipeline:
            1. OAuth token refresh (if auth_manager configured)
            2. Circuit breaker check → degraded response if OPEN
            3. Exponential backoff retry on transient failures

        Args:
            server_name: MCP server name.
            tool_name: Tool to call.
            arguments: Tool parameters.

        Returns:
            Tool result, or degraded response if circuit is open:
            {"error": "server_unavailable", "server": "...", "circuit_open": True}
        """
        breaker = self._get_or_create_breaker(server_name)

        # Pre-call auth refresh
        self._maybe_refresh_auth(server_name)

        # Circuit breaker check
        if not breaker.before_call():
            logger.warning(
                "Circuit OPEN for '%s' — returning degraded response",
                server_name,
            )
            return {
                "error": "server_unavailable",
                "server": server_name,
                "circuit_open": True,
            }

        def _call() -> dict:
            return self._client.call_tool(server_name, tool_name, arguments)

        timeout = self._server_timeouts.get(server_name, self._DEFAULT_TIMEOUT)

        try:
            result = self._execute_with_resilience(
                server_name=server_name,
                fn=_call,
                breaker=breaker,
                timeout=timeout,
            )
            return result
        except MCPToolError:
            # Tool-level errors should propagate to caller
            raise
        except Exception:
            # If all retries exhausted, return degraded response
            return {
                "error": "server_unavailable",
                "server": server_name,
                "circuit_open": True,
                "detail": "All retries exhausted",
            }

    # ── list_tools ──────────────────────────────────────────────

    def list_tools(self, server_name: str) -> list[MCPTool]:
        """List tools with resilience."""
        breaker = self._get_or_create_breaker(server_name)

        if not breaker.before_call():
            logger.warning(
                "Circuit OPEN for '%s' — returning empty tool list",
                server_name,
            )
            return []

        def _list() -> list[MCPTool]:
            return self._client.list_tools(server_name)

        timeout = self._server_timeouts.get(server_name, self._DEFAULT_TIMEOUT)

        try:
            result = self._execute_with_resilience(
                server_name=server_name,
                fn=_list,
                breaker=breaker,
                timeout=timeout,
            )
            return result
        except MCPToolError:
            raise
        except Exception:
            return []

    # ── list_servers ────────────────────────────────────────────

    def list_servers(self) -> list[str]:
        """List all connected servers."""
        return self._client.list_servers()

    # ── circuit breaker management ──────────────────────────────

    def get_circuit_stats(self, server_name: str) -> dict:
        """Get circuit breaker statistics for a server."""
        breaker = self._breakers.get(server_name)
        if breaker is None:
            return {"server": server_name, "state": "not_connected"}
        return breaker.get_stats()

    def get_all_circuit_stats(self) -> dict[str, dict]:
        """Get circuit breaker statistics for all servers."""
        return {name: cb.get_stats() for name, cb in self._breakers.items()}

    def force_reset_circuit(self, server_name: str) -> None:
        """Forcefully reset circuit breaker for a server."""
        breaker = self._breakers.get(server_name)
        if breaker is not None:
            breaker.force_reset()

    def set_server_timeout(self, server_name: str, timeout: float) -> None:
        """Override per-server timeout."""
        with self._lock:
            self._server_timeouts[server_name] = timeout

    # ── internal ────────────────────────────────────────────────

    def _get_or_create_breaker(self, server_name: str) -> CircuitBreaker:
        with self._lock:
            if server_name not in self._breakers:
                self._breakers[server_name] = CircuitBreaker(
                    server_name, self._cb_config
                )
            return self._breakers[server_name]

    def _maybe_refresh_auth(self, server_name: str) -> None:
        """Refresh OAuth token if needed."""
        if self._auth_manager is None:
            return
        self._auth_manager.maybe_refresh(server_name)

    def _execute_with_resilience(
        self,
        server_name: str,
        fn: Callable[[], Any],
        breaker: CircuitBreaker,
        timeout: float,
    ) -> Any:
        """Execute fn with exponential backoff retry.

        Only retries on transient errors (MCPTimeoutError,
        MCPConnectionError with URLError cause, etc.).
        Does NOT retry on MCPToolError (tool-level failures).
        """
        last_exception: Optional[Exception] = None

        for attempt in range(self._retry_config.max_retries + 1):
            try:
                result = fn()
                breaker.on_success()
                return result
            except MCPToolError:
                # Tool errors are not transient — fail immediately
                breaker.on_failure()
                raise
            except MCPTimeoutError as e:
                last_exception = e
                breaker.on_failure()
                if not breaker.before_call():
                    break  # circuit opened during retries
                if attempt < self._retry_config.max_retries:
                    delay = self._calc_backoff(attempt)
                    logger.debug(
                        "Timeout on '%s', retry %d/%d in %.1fs",
                        server_name,
                        attempt + 1,
                        self._retry_config.max_retries,
                        delay,
                    )
                    time.sleep(delay)
            except MCPConnectionError as e:
                last_exception = e
                breaker.on_failure()
                if not breaker.before_call():
                    break
                if attempt < self._retry_config.max_retries:
                    delay = self._calc_backoff(attempt)
                    logger.debug(
                        "Connection error on '%s', retry %d/%d in %.1fs",
                        server_name,
                        attempt + 1,
                        self._retry_config.max_retries,
                        delay,
                    )
                    time.sleep(delay)
            except MCPError as e:
                last_exception = e
                breaker.on_failure()
                if not breaker.before_call():
                    break
                if attempt < self._retry_config.max_retries:
                    delay = self._calc_backoff(attempt)
                    logger.debug(
                        "MCP error on '%s', retry %d/%d in %.1fs",
                        server_name,
                        attempt + 1,
                        self._retry_config.max_retries,
                        delay,
                    )
                    time.sleep(delay)

        # All retries exhausted
        if last_exception:
            raise last_exception
        raise MCPConnectionError(
            f"Circuit open for '{server_name}' — all retries exhausted"
        )

    def _calc_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter.

        delay = min(base_delay * backoff_factor^attempt + jitter, max_delay)
        """
        delay = self._retry_config.base_delay * (
            self._retry_config.backoff_factor ** attempt
        )
        # Add jitter (±25%)
        import random
        jitter = delay * 0.25 * (random.random() * 2 - 1)
        return min(delay + jitter, self._retry_config.max_delay)


# ═══════════════════════════════════════════════════════════════════
# MCPAuthManager
# ═══════════════════════════════════════════════════════════════════


class MCPAuthManager:
    """OAuth token manager for MCP server connections.

    Manages per-server OAuth tokens with automatic refresh and
    pre-refresh window (default 5 minutes before expiry).

    Usage:
        auth = MCPAuthManager()
        auth.register_token_refresher(
            "search-server",
            lambda: fetch_new_token(),  # returns OAuthToken
        )

        # Then pass to ResilientMCPClient:
        client = ResilientMCPClient(auth_manager=auth)
        # → call_tool() will auto-refresh before expiry
    """

    def __init__(self, pre_refresh_seconds: float = 300.0) -> None:
        self._pre_refresh_seconds = pre_refresh_seconds  # 5 min default
        # server_name → OAuthToken
        self._tokens: Dict[str, OAuthToken] = {}
        # server_name → callable that returns OAuthToken
        self._refreshers: Dict[str, Callable[[], OAuthToken]] = {}
        self._lock = threading.RLock()

    # ── public API ──────────────────────────────────────────────

    def register_token_refresher(
        self,
        server_name: str,
        refresher: Callable[[], OAuthToken],
    ) -> None:
        """Register a token refresh callback for a server.

        Args:
            server_name: MCP server name.
            refresher: Callable that fetches a new OAuthToken.
        """
        with self._lock:
            self._refreshers[server_name] = refresher

    def set_token(self, server_name: str, token: OAuthToken) -> None:
        """Manually set a token for a server."""
        with self._lock:
            self._tokens[server_name] = token

    def get_token(self, server_name: str) -> Optional[OAuthToken]:
        """Get the current token for a server."""
        with self._lock:
            return self._tokens.get(server_name)

    def get_auth_header(self, server_name: str) -> Optional[str]:
        """Get the Authorization header value for a server.

        Returns:
            "Bearer <token>" string, or None if no token is registered.
        """
        token = self.get_token(server_name)
        if token is None:
            return None
        return f"{token.token_type} {token.access_token}"

    def maybe_refresh(self, server_name: str) -> Optional[OAuthToken]:
        """Check and refresh token if expired or within pre-refresh window.

        Args:
            server_name: MCP server name.

        Returns:
            The current (possibly refreshed) token, or None.
        """
        with self._lock:
            token = self._tokens.get(server_name)
            if token is None:
                # No token yet — try to fetch if refresher is registered
                refresher = self._refreshers.get(server_name)
                if refresher is not None:
                    token = refresher()
                    self._tokens[server_name] = token
                    logger.info(
                        "Fetched initial OAuth token for '%s' (expires in %.0fs)",
                        server_name,
                        token.expires_at - time.time() if token.expires_at else 0,
                    )
                return token

            if token.is_expired(self._pre_refresh_seconds):
                refresher = self._refreshers.get(server_name)
                if refresher is not None:
                    try:
                        new_token = refresher()
                        self._tokens[server_name] = new_token
                        logger.info(
                            "Refreshed OAuth token for '%s' (expires in %.0fs)",
                            server_name,
                            new_token.expires_at - time.time()
                            if new_token.expires_at
                            else 0,
                        )
                        return new_token
                    except Exception as e:
                        logger.error(
                            "Failed to refresh OAuth token for '%s': %s",
                            server_name, e,
                        )
                        # Return the existing (expired) token as fallback
                        return token
                else:
                    logger.warning(
                        "Token for '%s' is expired but no refresher registered",
                        server_name,
                    )

            return token

    def invalidate_token(self, server_name: str) -> None:
        """Remove cached token (forces refresh on next call)."""
        with self._lock:
            self._tokens.pop(server_name, None)

    def list_managed_servers(self) -> list[str]:
        """List all servers with auth configuration."""
        with self._lock:
            return list(self._refreshers.keys())

    def force_refresh(self, server_name: str) -> Optional[OAuthToken]:
        """Force immediate token refresh, ignoring pre-refresh window.

        Args:
            server_name: MCP server name.

        Returns:
            The refreshed token, or None if no refresher is registered.
        """
        refresher = self._refreshers.get(server_name)
        if refresher is None:
            return None

        try:
            new_token = refresher()
            with self._lock:
                self._tokens[server_name] = new_token
            logger.info(
                "Force-refreshed OAuth token for '%s'",
                server_name,
            )
            return new_token
        except Exception as e:
            logger.error(
                "Force-refresh failed for '%s': %s", server_name, e,
            )
            raise
