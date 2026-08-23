"""
Tests for jarvis.mcp.circuit_breaker — MCP Circuit Breaker Module.

Covers:
  - CircuitBreaker: state transitions, sliding window, force_reset, stats
  - ResilientMCPClient: degraded response, exponential backoff retry,
    per-server timeout, per-server circuit isolation, integration with MCPClient
  - MCPAuthManager: token refresh, pre-refresh window, force_refresh,
    invalidate_token, auth header generation
  - Full pipeline: circuit open → degraded response, half_open → recovery
  - CircuitBreakerConfig / RetryConfig / OAuthToken data classes
"""

import os
import sys
import time
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jarvis.mcp.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    ResilientMCPClient,
    MCPAuthManager,
    OAuthToken,
    CircuitBreakerConfig,
    RetryConfig,
)

from jarvis.mcp_client import (
    MCPClient,
    MCPServerConfig,
    MCPTool,
    MCPToolError,
    MCPConnectionError,
    MCPTimeoutError,
)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


class _CountingMockClient(MCPClient):
    """Mock MCPClient that counts calls and can be configured to fail."""

    def __init__(self):
        super().__init__()
        self.call_count = 0
        self.connect_count = 0
        self._fail_count = -1  # -1 = never fail
        self._fail_pattern: list[type[Exception]] = []  # sequential failures
        self._last_arguments: dict = {}
        self._tool_results: dict = {}  # tool_name → result override

    def set_fail_count(self, n: int):
        """Fail the next n calls, then succeed."""
        self._fail_count = n

    def set_fail_pattern(self, exceptions: list[type[Exception]]):
        """Fail sequentially with these exception types, then succeed."""
        self._fail_pattern = list(exceptions)

    def connect(self, config: MCPServerConfig) -> bool:
        self.connect_count += 1
        if self._fail_count > 0 or self._fail_pattern:
            return self._fail_or_ok("connect")
        return super().connect(config)

    def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        self.call_count += 1
        self._last_arguments = arguments

        if tool_name in self._tool_results:
            return self._tool_results[tool_name]

        if self._fail_count > 0 or self._fail_pattern:
            return self._fail_or_ok("call_tool")

        return {"result": f"mock:{tool_name}", "raw": {}}

    def _fail_or_ok(self, context: str):
        """Either fail or succeed based on configured pattern."""
        if self._fail_pattern:
            exc_type = self._fail_pattern.pop(0)
            if exc_type is MCPTimeoutError:
                raise MCPTimeoutError(f"Mock timeout in {context}")
            elif exc_type is MCPConnectionError:
                raise MCPConnectionError(f"Mock connection error in {context}")
            elif exc_type is MCPToolError:
                raise MCPToolError(f"Mock tool error in {context}")
            else:
                raise MCPConnectionError(f"Mock error in {context}")
        elif self._fail_count > 0:
            self._fail_count -= 1
            raise MCPConnectionError(f"Mock failure {context} ({self._fail_count} left)")
        return True if context == "connect" else {"result": "ok", "raw": {}}


class _SlowMockClient(MCPClient):
    """Mock MCPClient that simulates slow responses (for timeout testing)."""

    def __init__(self, delay: float = 5.0):
        super().__init__()
        self.delay = delay

    def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        time.sleep(self.delay)
        return {"result": "slow-result", "raw": {}}


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def breaker():
    """Fresh CircuitBreaker with low thresholds for fast testing."""
    return CircuitBreaker(
        "test-server",
        CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=0.2,  # 200ms for fast tests
            sliding_window_seconds=10.0,
        ),
    )


@pytest.fixture
def cb_config():
    return CircuitBreakerConfig(
        failure_threshold=5,
        recovery_timeout=30.0,
        sliding_window_seconds=60.0,
    )


@pytest.fixture
def retry_config():
    return RetryConfig(
        max_retries=3,
        base_delay=0.01,   # tiny delay for fast tests
        max_delay=0.05,
        backoff_factor=2.0,
    )


@pytest.fixture
def counting_client():
    return _CountingMockClient()


@pytest.fixture
def resilient_client(counting_client, retry_config):
    return ResilientMCPClient(
        mcp_client=counting_client,
        cb_config=CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=0.2,
            sliding_window_seconds=10.0,
        ),
        retry_config=retry_config,
    )


@pytest.fixture
def auth_manager():
    return MCPAuthManager(pre_refresh_seconds=300.0)


# ═══════════════════════════════════════════════════════════════════
# CircuitBreaker State Transition Tests
# ═══════════════════════════════════════════════════════════════════


class TestCircuitBreakerStateTransitions:
    """Test CLOSED → OPEN → HALF_OPEN → CLOSED lifecycle."""

    def test_initial_state_closed(self, breaker):
        """New circuit breaker should be CLOSED."""
        assert breaker.state == CircuitState.CLOSED
        assert not breaker.is_open
        assert breaker.before_call() is True

    def test_transition_to_open_after_threshold(self, breaker):
        """Reaching failure_threshold should open the circuit."""
        # 3 failures → OPEN
        for _ in range(3):
            assert breaker.before_call() is True
            breaker.on_failure()

        assert breaker.state == CircuitState.OPEN
        assert breaker.is_open
        assert breaker.before_call() is False

    def test_transition_to_half_open_after_recovery_timeout(self, breaker):
        """After recovery_timeout, circuit should go HALF_OPEN."""
        # Open the circuit
        for _ in range(3):
            breaker.before_call()
            breaker.on_failure()
        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.25)  # > 200ms

        # Next before_call should transition to HALF_OPEN and allow
        assert breaker.before_call() is True
        assert breaker.state == CircuitState.HALF_OPEN

    def test_recovery_on_success_in_half_open(self, breaker):
        """Success in HALF_OPEN should reset to CLOSED."""
        # Open the circuit
        for _ in range(3):
            breaker.before_call()
            breaker.on_failure()
        assert breaker.state == CircuitState.OPEN

        # Wait for recovery
        time.sleep(0.25)
        assert breaker.before_call() is True
        assert breaker.state == CircuitState.HALF_OPEN

        # Success → CLOSED
        breaker.on_success()
        assert breaker.state == CircuitState.CLOSED

    def test_failure_in_half_open_reopens(self, breaker):
        """Failure in HALF_OPEN should go back to OPEN."""
        for _ in range(3):
            breaker.before_call()
            breaker.on_failure()
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.25)
        assert breaker.before_call() is True
        assert breaker.state == CircuitState.HALF_OPEN

        # Failure → OPEN again
        breaker.on_failure()
        assert breaker.state == CircuitState.OPEN


# ═══════════════════════════════════════════════════════════════════
# CircuitBreaker Sliding Window & Stats Tests
# ═══════════════════════════════════════════════════════════════════


class TestCircuitBreakerWindowAndStats:
    """Test sliding window and statistics."""

    def test_success_resets_consecutive_failures(self, breaker):
        """Success in CLOSED should reset consecutive failure count."""
        breaker.on_failure()
        breaker.on_failure()  # 2 failures, not enough to open
        breaker.on_success()  # resets

        # Should still be CLOSED
        assert breaker.state == CircuitState.CLOSED

    def test_stats_reflect_state(self, breaker):
        """get_stats should return accurate statistics."""
        breaker.on_failure()
        breaker.on_failure()
        breaker.on_success()

        stats = breaker.get_stats()
        assert stats["total_failures"] == 2
        assert stats["total_successes"] == 1
        assert stats["consecutive_failures"] == 0
        assert stats["state"] == "closed"

    def test_force_reset(self, breaker):
        """force_reset should return to CLOSED from any state."""
        for _ in range(3):
            breaker.before_call()
            breaker.on_failure()
        assert breaker.state == CircuitState.OPEN

        breaker.force_reset()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.before_call() is True


# ═══════════════════════════════════════════════════════════════════
# ResilientMCPClient — Degraded Response Tests
# ═══════════════════════════════════════════════════════════════════


class TestResilientClientDegraded:
    """Test degraded response when circuit is OPEN."""

    def test_degraded_response_when_circuit_open(self, counting_client, retry_config):
        """When circuit is OPEN, call_tool returns structured degraded response."""
        # Use a client that always fails
        counting_client.set_fail_count(99)  # essentially always fails

        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(
                failure_threshold=2,
                recovery_timeout=30.0,  # long recovery time
            ),
            retry_config=RetryConfig(max_retries=0),  # no retry — fail immediately
        )

        # First call: fail → circuit goes OPEN after 2 failures
        # We need 2 failures to hit threshold=2
        for _ in range(2):
            try:
                client.call_tool("test-srv", "search", {"q": "test"})
            except Exception:
                pass

        # Now circuit is OPEN → degraded response
        result = client.call_tool("test-srv", "search", {"q": "MCP"})
        assert result.get("error") == "server_unavailable"
        assert result.get("server") == "test-srv"
        assert result.get("circuit_open") is True

    def test_degraded_list_tools_when_open(self, counting_client, retry_config):
        """list_tools should return empty list when circuit is OPEN."""
        counting_client.set_fail_count(99)

        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=30.0),
            retry_config=RetryConfig(max_retries=0),
        )

        # Force circuit open
        for _ in range(2):
            try:
                client.list_tools("test-srv")
            except Exception:
                pass

        result = client.list_tools("test-srv")
        assert result == []


# ═══════════════════════════════════════════════════════════════════
# ResilientMCPClient — Retry & Timeout Tests
# ═══════════════════════════════════════════════════════════════════


class TestResilientClientRetry:
    """Test exponential backoff retry behavior."""

    def test_retry_on_transient_error_then_succeed(self, counting_client, retry_config):
        """Should retry on transient errors and eventually succeed."""
        # Fail twice with ConnectionError, then succeed
        counting_client.set_fail_pattern([MCPConnectionError, MCPConnectionError])

        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(
                failure_threshold=5,
                recovery_timeout=0.2,
            ),
            retry_config=retry_config,
        )

        result = client.call_tool("test-srv", "add", {"a": 1, "b": 2})
        assert "mock:add" in result.get("result", "")
        # 1 initial + 2 retries = 3 calls total (but pattern only has 2 failures,
        # so 3rd call succeeds)
        assert counting_client.call_count >= 3

    def test_no_retry_on_tool_error(self, counting_client, retry_config):
        """MCPToolError should NOT trigger retry — fail immediately."""
        counting_client.set_fail_pattern([MCPToolError])

        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(failure_threshold=5),
            retry_config=retry_config,
        )

        with pytest.raises(MCPToolError):
            client.call_tool("test-srv", "div", {"a": 1, "b": 0})

        # Only 1 call — no retry for tool errors
        assert counting_client.call_count == 1

    def test_all_retries_exhausted_returns_degraded(self, counting_client):
        """When all retries exhausted, return degraded response."""
        counting_client.set_fail_count(99)

        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(failure_threshold=10, recovery_timeout=30.0),
            retry_config=RetryConfig(max_retries=2, base_delay=0.01, max_delay=0.05),
        )

        result = client.call_tool("test-srv", "search", {"q": "test"})
        assert result.get("circuit_open") is True or result.get("error") == "server_unavailable"


# ═══════════════════════════════════════════════════════════════════
# ResilientMCPClient — Per-Server Isolation Tests
# ═══════════════════════════════════════════════════════════════════


class TestResilientClientIsolation:
    """Test that circuits are isolated per server."""

    def test_one_server_failure_does_not_affect_others(self, counting_client, retry_config):
        """Server A failing should not impact Server B."""
        # Server A: always fails
        counting_client.set_fail_count(99)

        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=30.0),
            retry_config=RetryConfig(max_retries=0),
        )

        # Exhaust server-a circuit
        for _ in range(2):
            try:
                client.call_tool("server-a", "tool", {})
            except Exception:
                pass

        # server-a is now OPEN
        stats_a = client.get_circuit_stats("server-a")
        assert stats_a["state"] == "open"

        # server-b should have no breaker yet
        stats_b = client.get_circuit_stats("server-b")
        assert stats_b["state"] == "not_connected"

    def test_force_reset_circuit(self, counting_client, retry_config):
        """force_reset_circuit should reset a specific server's breaker."""
        counting_client.set_fail_count(99)

        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=30.0),
            retry_config=RetryConfig(max_retries=0),
        )

        for _ in range(2):
            try:
                client.call_tool("srv", "tool", {})
            except Exception:
                pass

        assert client.get_circuit_stats("srv")["state"] == "open"

        client.force_reset_circuit("srv")
        assert client.get_circuit_stats("srv")["state"] == "closed"


# ═══════════════════════════════════════════════════════════════════
# MCPAuthManager Tests
# ═══════════════════════════════════════════════════════════════════


class TestMCPAuthManager:
    """Test OAuth token lifecycle management."""

    def test_get_token_returns_none_when_not_set(self, auth_manager):
        """Unset token should return None."""
        assert auth_manager.get_token("unknown") is None

    def test_set_and_get_token(self, auth_manager):
        """set_token then get_token should return the same token."""
        token = OAuthToken(
            access_token="abc123",
            token_type="Bearer",
            expires_at=time.time() + 3600,
            refresh_token="refresh-xyz",
        )
        auth_manager.set_token("srv", token)

        retrieved = auth_manager.get_token("srv")
        assert retrieved is not None
        assert retrieved.access_token == "abc123"
        assert retrieved.refresh_token == "refresh-xyz"

    def test_get_auth_header(self, auth_manager):
        """get_auth_header should return 'Bearer <token>'."""
        token = OAuthToken(access_token="secret")
        auth_manager.set_token("srv", token)

        header = auth_manager.get_auth_header("srv")
        assert header == "Bearer secret"

    def test_get_auth_header_none_when_no_token(self, auth_manager):
        """get_auth_header should return None when token not set."""
        assert auth_manager.get_auth_header("ghost") is None

    def test_token_not_expired_within_window(self, auth_manager):
        """Token with future expiry should not be considered expired."""
        token = OAuthToken(
            access_token="valid",
            expires_at=time.time() + 600,  # 10 min from now
        )
        auth_manager.set_token("srv", token)

        # pre_refresh_seconds = 300 (5 min), token expires in 10 min → not expired
        assert not token.is_expired(pre_refresh_seconds=300)

    def test_token_expired_within_pre_refresh_window(self, auth_manager):
        """Token within pre-refresh window should be considered expired."""
        token = OAuthToken(
            access_token="expiring",
            expires_at=time.time() + 120,  # 2 min from now
        )
        auth_manager.set_token("srv", token)

        # pre_refresh = 300s, token expires in 120s → expired for refresh purposes
        assert token.is_expired(pre_refresh_seconds=300)

    def test_token_past_expiry(self, auth_manager):
        """Token past absolute expiry should be expired."""
        token = OAuthToken(
            access_token="expired",
            expires_at=time.time() - 10,  # 10 seconds ago
        )
        assert token.is_expired(pre_refresh_seconds=0)

    def test_maybe_refresh_triggers_refresh_when_expired(self, auth_manager):
        """maybe_refresh should call refresher when token is due."""
        call_count = [0]

        def _refresher() -> OAuthToken:
            call_count[0] += 1
            return OAuthToken(
                access_token=f"new-token-{call_count[0]}",
                expires_at=time.time() + 3600,
            )

        auth_manager.register_token_refresher("srv", _refresher)

        # Set a token that is within pre-refresh window
        old_token = OAuthToken(
            access_token="old",
            expires_at=time.time() + 60,  # 1 min → within 5-min window
        )
        auth_manager.set_token("srv", old_token)

        # maybe_refresh should trigger refresh
        new_token = auth_manager.maybe_refresh("srv")
        assert call_count[0] == 1
        assert new_token is not None
        assert new_token.access_token == "new-token-1"

    def test_maybe_refresh_fetches_initial_token(self, auth_manager):
        """When no token exists, maybe_refresh should fetch if refresher registered."""
        fetch_count = [0]

        def _refresher():
            fetch_count[0] += 1
            return OAuthToken(access_token="initial", expires_at=time.time() + 3600)

        auth_manager.register_token_refresher("srv", _refresher)

        token = auth_manager.maybe_refresh("srv")
        assert fetch_count[0] == 1
        assert token is not None
        assert token.access_token == "initial"

    def test_force_refresh(self, auth_manager):
        """force_refresh should always call refresher regardless of expiry."""
        call_count = [0]

        def _refresher():
            call_count[0] += 1
            return OAuthToken(
                access_token=f"forced-{call_count[0]}",
                expires_at=time.time() + 3600,
            )

        auth_manager.register_token_refresher("srv", _refresher)

        # Set a valid token far from expiry
        token = OAuthToken(access_token="far-future", expires_at=time.time() + 7200)
        auth_manager.set_token("srv", token)

        # force_refresh should call refresher even though token is valid
        new_token = auth_manager.force_refresh("srv")
        assert call_count[0] == 1
        assert new_token.access_token == "forced-1"

    def test_invalidate_token(self, auth_manager):
        """invalidate_token should remove the cached token."""
        token = OAuthToken(access_token="remove-me")
        auth_manager.set_token("srv", token)
        assert auth_manager.get_token("srv") is not None

        auth_manager.invalidate_token("srv")
        assert auth_manager.get_token("srv") is None

    def test_list_managed_servers(self, auth_manager):
        """list_managed_servers should return servers with refreshers."""
        auth_manager.register_token_refresher("a", lambda: OAuthToken(access_token="a"))
        auth_manager.register_token_refresher("b", lambda: OAuthToken(access_token="b"))

        servers = auth_manager.list_managed_servers()
        assert "a" in servers
        assert "b" in servers
        assert len(servers) == 2

    def test_refresh_failure_returns_existing_token(self, auth_manager):
        """When refresh fails, maybe_refresh should return the existing token as fallback."""
        def _failing_refresher():
            raise RuntimeError("OAuth server down")

        auth_manager.register_token_refresher("srv", _failing_refresher)

        # Set an expired token
        old_token = OAuthToken(access_token="old-expired", expires_at=time.time() - 10)
        auth_manager.set_token("srv", old_token)

        # Refresh should fail but return existing token
        result = auth_manager.maybe_refresh("srv")
        assert result is not None
        assert result.access_token == "old-expired"


# ═══════════════════════════════════════════════════════════════════
# Data Class Tests
# ═══════════════════════════════════════════════════════════════════


class TestDataClasses:
    """Test configuration and token data classes."""

    def test_circuit_breaker_config_defaults(self):
        cfg = CircuitBreakerConfig()
        assert cfg.failure_threshold == 5
        assert cfg.recovery_timeout == 30.0
        assert cfg.half_open_max_requests == 1
        assert cfg.sliding_window_seconds == 60.0

    def test_retry_config_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.base_delay == 1.0
        assert cfg.max_delay == 30.0
        assert cfg.backoff_factor == 2.0

    def test_oauth_token_defaults(self):
        token = OAuthToken(access_token="test")
        assert token.access_token == "test"
        assert token.token_type == "Bearer"
        assert token.expires_at == 0.0
        assert token.refresh_token == ""
        assert token.scope == ""

    def test_oauth_token_no_expiry(self):
        """Token with expires_at=0 should not be considered expired."""
        token = OAuthToken(access_token="no-expiry")
        assert not token.is_expired()


# ═══════════════════════════════════════════════════════════════════
# Integration: CircuitBreaker + ResilientMCPClient Pipeline
# ═══════════════════════════════════════════════════════════════════


class TestFullPipeline:
    """End-to-end test: circuit open → degraded → recovery."""

    def test_full_pipeline_open_halfopen_closed(self, counting_client):
        """Full lifecycle: failures → OPEN → wait → HALF_OPEN → success → CLOSED."""
        # Fail 3 times (threshold=3), then succeed
        counting_client.set_fail_pattern([
            MCPConnectionError,
            MCPConnectionError,
            MCPConnectionError,
        ])

        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=0.3,
            ),
            retry_config=RetryConfig(max_retries=0),
        )

        # 3 failures → circuit opens
        for _ in range(3):
            try:
                client.call_tool("srv", "tool", {})
            except Exception:
                pass

        assert client.get_circuit_stats("srv")["state"] == "open"

        # While open → degraded response
        result = client.call_tool("srv", "tool", {})
        assert result.get("circuit_open") is True

        # Wait for recovery timeout
        time.sleep(0.4)

        # Now circuit should be HALF_OPEN and allow a call
        # The mock client now succeeds (fail_pattern exhausted)
        result = client.call_tool("srv", "tool", {})
        assert result.get("result") == "mock:tool"
        assert client.get_circuit_stats("srv")["state"] == "closed"

    def test_get_all_circuit_stats(self, counting_client, retry_config):
        """get_all_circuit_stats should return stats for all servers."""
        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(failure_threshold=5),
            retry_config=retry_config,
        )

        # Trigger calls on multiple servers
        client.call_tool("srv-a", "tool", {})
        client.call_tool("srv-b", "tool", {})

        all_stats = client.get_all_circuit_stats()
        assert "srv-a" in all_stats
        assert "srv-b" in all_stats
        assert all_stats["srv-a"]["state"] == "closed"
        assert all_stats["srv-b"]["state"] == "closed"


# ═══════════════════════════════════════════════════════════════════
# CircuitBreakerConfig edge cases
# ═══════════════════════════════════════════════════════════════════


class TestCircuitEdgeCases:
    """Edge cases for circuit breaker."""

    def test_threshold_zero_not_breaking_on_connect(self, counting_client):
        """Low threshold with retries should handle connect properly."""
        client = ResilientMCPClient(
            mcp_client=counting_client,
            cb_config=CircuitBreakerConfig(failure_threshold=1),
            retry_config=RetryConfig(max_retries=0),
        )

        # A single failure should open the circuit
        counting_client.set_fail_count(99)
        try:
            client.call_tool("srv", "tool", {})
        except Exception:
            pass

        stats = client.get_circuit_stats("srv")
        assert stats["state"] == "open"

    def test_set_server_timeout(self, counting_client):
        """Per-server timeout should be configurable."""
        client = ResilientMCPClient(mcp_client=counting_client)

        client.set_server_timeout("fast-srv", 0.5)
        client.set_server_timeout("slow-srv", 60.0)

        # Verify timeouts are stored (no direct getter, but call_tool uses them)
        assert client._server_timeouts["fast-srv"] == 0.5
        assert client._server_timeouts["slow-srv"] == 60.0

    def test_circuit_state_enum_values(self):
        """CircuitState enum should have expected values."""
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"
