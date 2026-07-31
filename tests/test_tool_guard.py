"""
Tests for jarvis.tool_guard — Tool Call Guard Middleware.

Covers:
  - InputValidator: SQL injection, path traversal, type/length checks
  - RateLimiter: sliding window, per-tool limits, retry_after
  - OutputFilter: PII detection, masking, sensitive keywords, truncation
  - ToolGuardMiddleware: wrap_tool_call pipeline, audit integration, governance
"""

import sys
import os
import time
import tempfile

import pytest

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jarvis.tool_guard import (
    InputValidator,
    ValidationSeverity,
    RateLimiter,
    RateLimitResult,
    OutputFilter,
    PIISeverity,
    ToolGuardMiddleware,
    GuardEventType,
    GuardResult,
)


# ═══════════════════════════════════════════════════════════════════
# InputValidator Tests
# ═══════════════════════════════════════════════════════════════════


class TestInputValidator:
    def test_clean_input_passes(self):
        v = InputValidator()
        result = v.validate({"name": "alice", "age": 30})
        assert result.passed
        assert not result.findings

    def test_sql_injection_keyword_detected(self):
        v = InputValidator()
        result = v.validate({"query": "DROP TABLE users"})
        assert not result.passed
        assert result.has_critical
        assert any(f.rule == "sql_injection_keyword" for f in result.findings)

    def test_sql_injection_pattern_detected(self):
        v = InputValidator()
        result = v.validate({"query": "SELECT * FROM x WHERE id = '1' OR '1'='1' --"})
        assert not result.passed
        assert any(f.rule == "sql_injection_pattern" for f in result.findings)

    def test_union_select_detected(self):
        v = InputValidator()
        result = v.validate({"q": "UNION SELECT password FROM admin"})
        assert not result.passed
        assert result.has_critical

    def test_path_traversal_detected(self):
        v = InputValidator()
        result = v.validate({"path": "../../../etc/passwd"})
        assert not result.passed
        assert any(f.rule == "path_traversal" for f in result.findings)

    def test_backslash_path_traversal_detected(self):
        v = InputValidator()
        result = v.validate({"path": "..\\..\\windows\\system32"})
        assert not result.passed
        assert any(f.rule == "path_traversal" for f in result.findings)

    def test_nested_dict_validation(self):
        v = InputValidator()
        result = v.validate({
            "user": {"name": "bob", "query": "DELETE FROM sessions"},
            "list": ["ok", "DROP DATABASE test"],
        })
        assert not result.passed
        assert result.has_critical

    def test_max_string_length_warning(self):
        v = InputValidator(max_string_length=10)
        result = v.validate({"text": "x" * 50})
        assert any(f.rule == "max_string_length" for f in result.findings)

    def test_disabled_checks(self):
        v = InputValidator(check_sql_injection=False, check_path_traversal=False)
        result = v.validate({"q": "DROP TABLE x", "p": "../../etc"})
        assert result.passed  # no critical findings


# ═══════════════════════════════════════════════════════════════════
# RateLimiter Tests
# ═══════════════════════════════════════════════════════════════════


class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = RateLimiter(max_calls=3, window_seconds=60)
        for _ in range(3):
            assert rl.check("tool_a").allowed

    def test_blocks_over_limit(self):
        rl = RateLimiter(max_calls=2, window_seconds=60)
        assert rl.check("tool_a").allowed
        assert rl.check("tool_a").allowed
        result = rl.check("tool_a")
        assert not result.allowed
        assert result.retry_after_seconds > 0

    def test_per_tool_isolation(self):
        rl = RateLimiter(max_calls=1, window_seconds=60)
        assert rl.check("tool_a").allowed
        assert rl.check("tool_b").allowed  # different tool, not limited
        assert not rl.check("tool_a").allowed  # same tool, now blocked

    def test_window_expiry(self):
        rl = RateLimiter(max_calls=1, window_seconds=0.1)
        assert rl.check("tool_a").allowed
        assert not rl.check("tool_a").allowed
        time.sleep(0.15)
        assert rl.check("tool_a").allowed  # window expired

    def test_reset(self):
        rl = RateLimiter(max_calls=1, window_seconds=60)
        assert rl.check("tool_a").allowed
        assert not rl.check("tool_a").allowed
        rl.reset("tool_a")
        assert rl.check("tool_a").allowed

    def test_get_stats(self):
        rl = RateLimiter(max_calls=5, window_seconds=60)
        rl.check("tool_x")
        rl.check("tool_x")
        stats = rl.get_stats()
        assert "tool_x" in stats
        assert stats["tool_x"]["current_count"] == 2


# ═══════════════════════════════════════════════════════════════════
# OutputFilter Tests
# ═══════════════════════════════════════════════════════════════════


class TestOutputFilter:
    def test_clean_output_passes(self):
        f = OutputFilter()
        result = f.filter("Hello, this is a normal message.")
        assert result.passed
        assert result.output == "Hello, this is a normal message."

    def test_email_detected_and_masked(self):
        f = OutputFilter()
        result = f.filter("Contact alice@example.com for help")
        assert result.pii_matches
        assert any(m.pii_type == "email" for m in result.pii_matches)
        assert "alice@example.com" not in result.output
        assert "@example.com" in result.output

    def test_phone_detected(self):
        f = OutputFilter()
        result = f.filter("Call 13800138000 now")
        assert any(m.pii_type == "phone_cn" for m in result.pii_matches)

    def test_ssn_detected_high_severity(self):
        f = OutputFilter()
        result = f.filter("SSN: 123-45-6789")
        ssn = [m for m in result.pii_matches if m.pii_type == "ssn"]
        assert ssn
        assert ssn[0].severity == PIISeverity.HIGH
        assert not result.passed  # HIGH PII fails filter

    def test_credit_card_detected(self):
        f = OutputFilter()
        result = f.filter("Card: 4111 1111 1111 1111")
        assert any(m.pii_type == "credit_card" for m in result.pii_matches)

    def test_id_card_detected(self):
        f = OutputFilter()
        result = f.filter("ID: 11010119900307651X")
        assert any(m.pii_type == "id_card_cn" for m in result.pii_matches)

    def test_sensitive_keyword_detected(self):
        f = OutputFilter()
        result = f.filter("The password is secret123")
        assert "password" in result.sensitive_keywords_found

    def test_truncation(self):
        f = OutputFilter(max_output_length=20)
        result = f.filter("x" * 100)
        assert result.truncated
        assert len(result.output) == 20

    def test_no_masking_mode(self):
        f = OutputFilter(mask_pii=False)
        result = f.filter("Email alice@example.com here")
        assert "alice@example.com" in result.output  # not masked
        assert result.pii_matches  # still detected


# ═══════════════════════════════════════════════════════════════════
# ToolGuardMiddleware Tests
# ═══════════════════════════════════════════════════════════════════


class TestToolGuardMiddleware:
    def test_wrap_passes_clean_call(self):
        guard = ToolGuardMiddleware()
        calls = []

        def my_tool(x):
            calls.append(x)
            return f"result:{x}"

        wrapped = guard.wrap_tool_call("my_tool", my_tool)
        result = wrapped(42)
        assert isinstance(result, GuardResult)
        assert result.success
        assert result.output == "result:42"
        assert calls == [42]

    def test_wrap_blocks_sql_injection(self):
        guard = ToolGuardMiddleware()
        calls = []

        def query_tool(sql):
            calls.append(sql)
            return "ok"

        wrapped = guard.wrap_tool_call("query_tool", query_tool)
        result = wrapped("DROP TABLE users")
        assert not result.success
        assert result.blocked
        assert result.block_reason == "input_validation_failed"
        assert calls == []  # not executed

    def test_wrap_blocks_path_traversal(self):
        guard = ToolGuardMiddleware()
        calls = []

        def read_tool(path):
            calls.append(path)
            return "file content"

        wrapped = guard.wrap_tool_call("read_tool", read_tool)
        result = wrapped("../../etc/passwd")
        assert not result.success
        assert result.blocked
        assert calls == []

    def test_wrap_rate_limits(self):
        guard = ToolGuardMiddleware(rate_limiter=RateLimiter(max_calls=2, window_seconds=60))
        calls = []

        def api_tool(x):
            calls.append(x)
            return x * 2

        wrapped = guard.wrap_tool_call("api_tool", api_tool)
        assert wrapped(1).success
        assert wrapped(2).success
        result = wrapped(3)
        assert not result.success
        assert result.block_reason == "rate_limited"
        assert len(calls) == 2

    def test_wrap_filters_pii_output(self):
        guard = ToolGuardMiddleware()
        calls = []

        def info_tool():
            calls.append(1)
            return "User SSN: 123-45-6789"

        wrapped = guard.wrap_tool_call("info_tool", info_tool)
        result = wrapped()
        assert result.success
        assert "123-45-6789" not in result.output
        assert calls == [1]

    def test_wrap_propagates_exception(self):
        guard = ToolGuardMiddleware()

        def failing_tool():
            raise ValueError("boom")

        wrapped = guard.wrap_tool_call("failing_tool", failing_tool)
        result = wrapped()
        assert not result.success
        assert "boom" in result.error

    def test_disabled_guard_passes_through(self):
        guard = ToolGuardMiddleware(enabled=False)
        calls = []

        def tool(x):
            calls.append(x)
            return x

        wrapped = guard.wrap_tool_call("tool", tool)
        result = wrapped("DROP TABLE x")  # would normally block
        assert result == "DROP TABLE x"  # raw passthrough
        assert calls == ["DROP TABLE x"]

    def test_audit_integration(self):
        # Use a simple in-memory audit logger stub
        audit_events = []

        class StubAudit:
            def log(self, **kwargs):
                audit_events.append(kwargs)

        guard = ToolGuardMiddleware(audit_logger=StubAudit())
        wrapped = guard.wrap_tool_call("t", lambda x: f"v:{x}")
        wrapped(1)
        assert audit_events  # at least one audit event written

    def test_governance_integration_on_pii(self):
        gov_calls = []

        class StubGov:
            def validate(self, action, context=None):
                gov_calls.append((action, context))
                return type("R", (), {"passed": True})()

        guard = ToolGuardMiddleware(governance_agent=StubGov())
        wrapped = guard.wrap_tool_call("t", lambda: "SSN: 123-45-6789")
        result = wrapped()
        assert result.success
        assert gov_calls  # governance was notified about PII

    def test_stats_tracking(self):
        guard = ToolGuardMiddleware()
        wrapped = guard.wrap_tool_call("t", lambda x: x)
        wrapped(1)
        wrapped(2)
        stats = guard.get_stats()
        assert stats["total_calls"] == 2
        assert stats["blocked_calls"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
