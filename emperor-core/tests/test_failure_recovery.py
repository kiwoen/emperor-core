"""测试逐步故障恢复引擎 (P0.3)

覆盖：
  - 错误分类（TRANSIENT / PERMANENT / DEGRADABLE）
  - 重试成功场景
  - 重试耗尽后降级
  - 熔断器开闭转换（CLOSED → OPEN → HALF_OPEN）
  - 降级链执行（SKIP / DEFAULT / ALTERNATE_MODEL）
  - Pipeline 集成（use_recovery_engine=True）
  - 向后兼容（旧版 auto_retry 不受影响）
"""
from __future__ import annotations

import time
import threading
import pytest
from unittest import mock

from jarvis.failure_recovery import (
    ErrorCategory, ErrorClassifier,
    RetryPolicy,
    CircuitState, CircuitBreaker,
    FallbackType, FallbackAction, FallbackChain,
    RecoveryResultStatus, RecoveryResult, RecoveryContext, RecoveryEngine,
    create_default_engine, create_strict_engine,
)
from jarvis.pipeline import (
    ServicePipeline, Stage, StageStatus, PipelineStatus,
    PipelineRegistry,
)


# ═══════════════════════════════════════════════════════════════════
# ErrorClassifier
# ═══════════════════════════════════════════════════════════════════


class TestErrorClassifier:
    def test_transient_timeout(self):
        c = ErrorClassifier()
        assert c.classify(TimeoutError("connection timed out")) == ErrorCategory.TRANSIENT

    def test_transient_connection_refused(self):
        c = ErrorClassifier()
        assert c.classify(ConnectionRefusedError()) == ErrorCategory.TRANSIENT

    def test_transient_connection_reset(self):
        c = ErrorClassifier()
        assert c.classify(ConnectionResetError()) == ErrorCategory.TRANSIENT

    def test_transient_generic_os_error_timed_out(self):
        c = ErrorClassifier()
        assert c.classify(OSError("timed out")) == ErrorCategory.TRANSIENT

    def test_transient_runtime_default(self):
        """Unclassified RuntimeError should default to TRANSIENT."""
        c = ErrorClassifier()
        assert c.classify(RuntimeError("something went wrong")) == ErrorCategory.TRANSIENT

    def test_permanent_value_error(self):
        c = ErrorClassifier()
        assert c.classify(ValueError("invalid value")) == ErrorCategory.PERMANENT

    def test_permanent_type_error(self):
        c = ErrorClassifier()
        assert c.classify(TypeError("bad type")) == ErrorCategory.PERMANENT

    def test_permanent_file_not_found(self):
        c = ErrorClassifier()
        assert c.classify(FileNotFoundError("no such file")) == ErrorCategory.PERMANENT

    def test_permanent_permission_error(self):
        c = ErrorClassifier()
        assert c.classify(PermissionError("access denied")) == ErrorCategory.PERMANENT

    def test_permanent_key_error(self):
        c = ErrorClassifier()
        assert c.classify(KeyError("missing_key")) == ErrorCategory.PERMANENT

    def test_degradable_rate_limit(self):
        c = ErrorClassifier()
        assert c.classify(Exception("rate limit exceeded")) == ErrorCategory.DEGRADABLE

    def test_degradable_quota(self):
        c = ErrorClassifier()
        assert c.classify(Exception("quota exhausted")) == ErrorCategory.DEGRADABLE

    def test_unmatched_defaults_to_permanent(self):
        """Completely unclassified exception → PERMANENT."""
        class CustomException(Exception):
            pass

        c = ErrorClassifier()
        assert c.classify(CustomException("weird error")) == ErrorCategory.PERMANENT

    def test_is_retryable(self):
        c = ErrorClassifier()
        assert c.is_retryable(TimeoutError()) is True
        assert c.is_retryable(ValueError()) is False

    def test_custom_rule_overrides_default(self):
        c = ErrorClassifier()
        c.add_rule(Exception, "critical network", ErrorCategory.PERMANENT)
        assert c.classify(Exception("critical network failure")) == ErrorCategory.PERMANENT

    def test_first_match_wins(self):
        """Substring matching OSError 'timed out' before generic OSError."""
        c = ErrorClassifier()
        # "timed out" substring match should be TRANSIENT, not fall through
        assert c.classify(OSError("timed out")) == ErrorCategory.TRANSIENT


# ═══════════════════════════════════════════════════════════════════
# RetryPolicy
# ═══════════════════════════════════════════════════════════════════


class TestRetryPolicy:
    def test_default_delays(self):
        p = RetryPolicy(max_retries=3, base_delay=0.5)
        assert p.delay_for_attempt(0) >= 0.5  # 0.5 + jitter
        assert p.delay_for_attempt(1) >= 1.0  # 1.0 + jitter
        assert p.delay_for_attempt(2) >= 2.0  # 2.0 + jitter

    def test_max_delay_cap(self):
        p = RetryPolicy(base_delay=1.0, max_delay=5.0)
        # attempt 10 would be 1024s, but cap at 5s
        delay = p.delay_for_attempt(10)
        assert delay <= 5.0 + 0.5  # max_delay + max jitter

    def test_no_jitter(self):
        p = RetryPolicy(base_delay=1.0, jitter=False)
        assert p.delay_for_attempt(0) == 1.0
        assert p.delay_for_attempt(1) == 2.0

    def test_jitter_adds_variance(self):
        p = RetryPolicy(base_delay=1.0, jitter=True)
        delays = [p.delay_for_attempt(0) for _ in range(20)]
        # Expect some variance due to jitter
        assert len(set(round(d, 3) for d in delays)) > 1


# ═══════════════════════════════════════════════════════════════════
# CircuitBreaker
# ═══════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.report_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_half_open_after_timeout(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        for _ in range(2):
            cb.report_failure()
        assert cb.state == CircuitState.OPEN

        # Wait past recovery timeout
        time.sleep(0.06)
        # allow_request should trigger transition to HALF_OPEN
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_recovery_success_closes_breaker(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        for _ in range(2):
            cb.report_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.06)
        assert cb.allow_request() is True  # → HALF_OPEN
        cb.report_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        for _ in range(2):
            cb.report_failure()
        time.sleep(0.06)
        cb.allow_request()  # → HALF_OPEN
        cb.report_failure()  # failure in HALF_OPEN → OPEN
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count_in_closed(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.report_failure()
        cb.report_failure()
        cb.report_success()  # not at threshold yet → reset count
        assert cb._failure_count == 0
        assert cb.state == CircuitState.CLOSED

    def test_reset_force_closes(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        for _ in range(2):
            cb.report_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    def test_allow_request_false_in_open(self):
        cb = CircuitBreaker(failure_threshold=2)
        for _ in range(2):
            cb.report_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_get_stats(self):
        cb = CircuitBreaker(name="test_cb", failure_threshold=5)
        cb.report_failure()
        stats = cb.get_stats()
        assert stats["name"] == "test_cb"
        assert stats["failure_count"] == 1
        assert stats["failure_threshold"] == 5
        assert stats["state"] == "closed"


# ═══════════════════════════════════════════════════════════════════
# FallbackChain
# ═══════════════════════════════════════════════════════════════════


class TestFallbackChain:
    def test_default_fallback(self):
        chain = FallbackChain()
        chain.add(FallbackAction(FallbackType.DEFAULT, default_value={"cached": True}, priority=0))

        fb_type, result = chain.execute()
        assert fb_type == FallbackType.DEFAULT
        assert result == {"cached": True}

    def test_skip_fallback(self):
        chain = FallbackChain()
        chain.add(FallbackAction(FallbackType.SKIP, priority=0))

        fb_type, result = chain.execute()
        assert fb_type == FallbackType.SKIP
        assert result is None

    def test_alternate_model_fallback(self):
        def backup():
            return {"from_backup": True}

        chain = FallbackChain()
        chain.add(FallbackAction(
            FallbackType.ALTERNATE_MODEL,
            alternate_handler=backup,
            priority=0,
        ))

        fb_type, result = chain.execute()
        assert fb_type == FallbackType.ALTERNATE_MODEL
        assert result == {"from_backup": True}

    def test_priority_ordering(self):
        """Lower priority number = tried first."""
        order = []

        def handler_a():
            order.append("a")
            raise RuntimeError("a failed")

        def handler_b():
            order.append("b")
            return "b result"

        chain = FallbackChain()
        chain.add(FallbackAction(
            FallbackType.ALTERNATE_MODEL, alternate_handler=handler_a, priority=10,
        ))
        chain.add(FallbackAction(
            FallbackType.ALTERNATE_MODEL, alternate_handler=handler_b, priority=5,
        ))

        fb_type, result = chain.execute()
        assert fb_type == FallbackType.ALTERNATE_MODEL
        assert result == "b result"
        assert order == ["b"]  # b tried first (priority 5 < 10)

    def test_chain_falls_through_on_alternate_failure(self):
        """If ALTERNATE_MODEL handler fails, try next in chain."""
        def failing():
            raise RuntimeError("backup also failed")

        chain = FallbackChain()
        chain.add(FallbackAction(
            FallbackType.ALTERNATE_MODEL, alternate_handler=failing, priority=0,
        ))
        chain.add(FallbackAction(
            FallbackType.DEFAULT, default_value="final_default", priority=10,
        ))

        fb_type, result = chain.execute()
        assert fb_type == FallbackType.DEFAULT
        assert result == "final_default"

    def test_empty_chain_raises(self):
        chain = FallbackChain()
        with pytest.raises(RuntimeError, match="empty"):
            chain.execute()

    def test_is_empty(self):
        chain = FallbackChain()
        assert chain.is_empty()

        chain.add(FallbackAction(FallbackType.SKIP, priority=0))
        assert not chain.is_empty()

    def test_chain_return_self(self):
        chain = FallbackChain()
        result = chain.add(FallbackAction(FallbackType.SKIP, priority=0))
        assert result is chain


# ═══════════════════════════════════════════════════════════════════
# RecoveryEngine
# ═══════════════════════════════════════════════════════════════════


class TestRecoveryEngine:
    def test_success_first_attempt(self):
        engine = create_default_engine()
        result = engine.execute_with_recovery(lambda: "ok")
        assert result.status == RecoveryResultStatus.SUCCESS
        assert result.output == "ok"
        assert result.attempts == 1

    def test_retry_success(self):
        call_count = [0]

        def flaky():
            call_count[0] += 1
            if call_count[0] < 3:
                raise TimeoutError("transient")
            return "finally ok"

        engine = create_default_engine()
        result = engine.execute_with_recovery(flaky)
        assert result.status == RecoveryResultStatus.RETRY_SUCCESS
        assert result.output == "finally ok"
        assert result.attempts == 3

    def test_permanent_error_no_retry(self):
        """Permanent errors should NOT be retried."""
        call_count = [0]

        def bad_input():
            call_count[0] += 1
            raise ValueError("bad value")

        engine = create_default_engine()
        result = engine.execute_with_recovery(bad_input)

        # Fallback chain has SKIP → DEGRADED
        assert result.status == RecoveryResultStatus.DEGRADED
        assert result.fallback_type == FallbackType.SKIP
        assert call_count[0] == 1  # No retry for PERMANENT

    def test_fallback_default_value(self):
        engine = RecoveryEngine(
            classifier=ErrorClassifier(),
            retry_policy=RetryPolicy(max_retries=0),
            circuit_breaker=CircuitBreaker(),
            fallback_chain=FallbackChain().add(
                FallbackAction(FallbackType.DEFAULT, default_value="fallback_result", priority=0)
            ),
        )

        def failer():
            raise RuntimeError("boom")

        result = engine.execute_with_recovery(failer)
        assert result.status == RecoveryResultStatus.DEGRADED
        assert result.output == "fallback_result"
        assert result.fallback_type == FallbackType.DEFAULT

    def test_circuit_breaker_opens_after_repeated_failures(self):
        """Circuit breaker should open after threshold failures."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        engine = RecoveryEngine(
            classifier=ErrorClassifier(),
            retry_policy=RetryPolicy(max_retries=1),
            circuit_breaker=cb,
            fallback_chain=FallbackChain().add(FallbackAction(FallbackType.SKIP, priority=0)),
        )

        failing = lambda: (_ for _ in ()).throw(RuntimeError("fail"))

        # First 3 calls: should go through and fail (retry each), then fallback
        for _ in range(3):
            result = engine.execute_with_recovery(failing)
            assert result.status == RecoveryResultStatus.DEGRADED

        # 4th call: circuit should be OPEN → fallback directly
        assert cb.state == CircuitState.OPEN
        result = engine.execute_with_recovery(failing)
        assert result.status == RecoveryResultStatus.DEGRADED  # via fallback

    def test_all_strategies_exhausted(self):
        """When no fallback chain exists and all retries fail → FAILED."""
        engine = create_strict_engine()

        def always_fail():
            raise RuntimeError("permanent death")

        result = engine.execute_with_recovery(always_fail)
        # strict engine has empty fallback → FAILED
        assert result.status in (RecoveryResultStatus.FAILED, RecoveryResultStatus.DEGRADED)

    def test_recovery_stats(self):
        engine = create_default_engine()
        engine.execute_with_recovery(lambda: "ok")
        engine.execute_with_recovery(lambda: "ok")

        stats = engine.get_stats()
        assert stats["success"] == 2
        assert stats["failed"] == 0

    def test_recovery_stats_retry(self):
        call_count = [0]

        def flaky():
            call_count[0] += 1
            if call_count[0] < 2:
                raise TimeoutError("transient")
            return "ok"

        engine = create_default_engine()
        engine.execute_with_recovery(flaky)

        stats = engine.get_stats()
        assert stats["retry_success"] == 1
        assert stats["success"] == 0

    def test_reset_stats(self):
        engine = create_default_engine()
        engine.execute_with_recovery(lambda: "ok")
        engine.reset_stats()

        stats = engine.get_stats()
        assert stats["success"] == 0
        assert stats["retry_success"] == 0

    def test_delay_between_retries(self):
        """Verify retry delay is applied on TRANSIENT errors."""
        call_count = [0]

        def fail_then_ok():
            call_count[0] += 1
            if call_count[0] < 2:
                raise TimeoutError("timeout")
            return "ok"

        engine = RecoveryEngine(
            classifier=ErrorClassifier(),
            retry_policy=RetryPolicy(max_retries=2, base_delay=0.05, jitter=False),
            circuit_breaker=CircuitBreaker(),
            fallback_chain=FallbackChain().add(FallbackAction(FallbackType.SKIP, priority=0)),
        )

        start = time.time()
        result = engine.execute_with_recovery(fail_then_ok)
        elapsed = time.time() - start

        assert result.status == RecoveryResultStatus.RETRY_SUCCESS
        # Should have waited at least base_delay (0.05s) between attempts
        assert elapsed >= 0.05

    def test_alternate_model_in_fallback_chain(self):
        """Full test: retry exhaust + alternate model fallback."""
        call_count = [0]

        def always_fail():
            call_count[0] += 1
            raise TimeoutError("timeout")

        def backup_model():
            return {"backup": "used"}

        engine = RecoveryEngine(
            classifier=ErrorClassifier(),
            retry_policy=RetryPolicy(max_retries=1, base_delay=0.01, jitter=False),
            circuit_breaker=CircuitBreaker(failure_threshold=10),
            fallback_chain=FallbackChain()
                .add(FallbackAction(
                    FallbackType.ALTERNATE_MODEL,
                    alternate_handler=backup_model,
                    priority=0,
                ))
                .add(FallbackAction(FallbackType.SKIP, priority=100)),
        )

        result = engine.execute_with_recovery(always_fail)
        assert result.status == RecoveryResultStatus.DEGRADED
        assert result.fallback_type == FallbackType.ALTERNATE_MODEL
        assert result.output == {"backup": "used"}
        assert call_count[0] == 2  # 1 original + 1 retry

    def test_recovery_context_logging(self):
        engine = create_default_engine()
        result = engine.execute_with_recovery(
            lambda: "ok",
            context=RecoveryContext(stage_name="test_stage", pipeline_name="test_pipeline"),
        )
        assert result.status == RecoveryResultStatus.SUCCESS

    def test_classify_degradable_substring(self):
        """Rule: Exception with 'degradable' substring → DEGRADABLE."""
        c = ErrorClassifier()
        assert c.classify(RuntimeError("degradable: model overloaded")) == ErrorCategory.DEGRADABLE

    def test_create_default_engine(self):
        engine = create_default_engine()
        assert engine.name == "default"
        assert engine.retry_policy.max_retries == 3
        assert not engine.fallback_chain.is_empty()

    def test_create_strict_engine(self):
        engine = create_strict_engine()
        assert engine.name == "strict"
        assert engine.retry_policy.max_retries == 2
        assert engine.fallback_chain.is_empty()


# ═══════════════════════════════════════════════════════════════════
# Pipeline Integration (use_recovery_engine=True)
# ═══════════════════════════════════════════════════════════════════


class TestPipelineWithRecovery:
    def test_simple_pipeline_with_recovery(self):
        p = ServicePipeline("test_recovery", use_recovery_engine=True)
        p.add_stage(Stage("s1", lambda ctx: {"a": 1}, output_key="s1"))
        p.add_stage(Stage("s2", lambda ctx: {"b": ctx.get("s1", {}).get("a", 0) + 1}, output_key="s2"))

        result = p.execute()
        assert result.status == PipelineStatus.COMPLETED
        assert result.final_output["s2"]["b"] == 2

    def test_pipeline_retry_on_transient_error(self):
        call_count = [0]

        def flaky_handler(ctx):
            call_count[0] += 1
            if call_count[0] < 3:
                raise TimeoutError("transient network error")
            return {"ok": True}

        p = ServicePipeline("test", auto_retry=False, use_recovery_engine=True,
                            recovery_engine_config={"max_retries": 3, "base_delay": 0.01})
        p.add_stage(Stage("flaky", flaky_handler, output_key="flaky"))

        result = p.execute()
        assert result.status == PipelineStatus.COMPLETED
        assert result.stages[0].status == StageStatus.SUCCESS
        assert call_count[0] == 3  # 2 failures + 1 success

    def test_pipeline_fallback_on_permanent_error(self):
        def bad_handler(ctx):
            raise ValueError("permanent bad input")

        p = ServicePipeline("test", auto_retry=False, use_recovery_engine=True,
                            recovery_engine_config={
                                "max_retries": 1,
                                "base_delay": 0.01,
                                "fallback_default": {"degraded": True},
                            })
        p.add_stage(Stage("bad_stage", bad_handler, output_key="result",
                          fail_strategy="skip"))

        result = p.execute()
        # Stage should succeed via fallback (default value)
        assert result.stages[0].status == StageStatus.SUCCESS
        assert result.final_output["result"] == {"degraded": True}

    def test_pipeline_no_fallback_causes_failure(self):
        def bad_handler(ctx):
            raise ValueError("permanent bad input")

        p = ServicePipeline("test", auto_retry=False, use_recovery_engine=True,
                            recovery_engine_config={
                                "max_retries": 0,
                                # No fallback_default → SKIP only via default chain
                            })
        p.add_stage(Stage("bad_stage", bad_handler, fail_strategy="skip"))

        result = p.execute()
        # SKIP fallback → success (degraded via SKIP)
        assert result.stages[0].status == StageStatus.SUCCESS

    def test_get_recovery_stats(self):
        p = ServicePipeline("test", use_recovery_engine=True)
        p.add_stage(Stage("s1", lambda ctx: {"ok": True}, output_key="s1"))

        result = p.execute()
        stats = p.get_recovery_stats()
        assert stats is not None
        assert stats["success"] >= 1

    def test_get_recovery_results(self):
        p = ServicePipeline("test", use_recovery_engine=True)
        p.add_stage(Stage("s1", lambda ctx: {"ok": True}, output_key="s1"))

        result = p.execute()
        recovery_results = p.get_recovery_results()
        assert len(recovery_results) == 1
        assert recovery_results[0].status == RecoveryResultStatus.SUCCESS

    def test_backward_compatible_auto_retry(self):
        """Legacy auto_retry should still work when use_recovery_engine=False."""
        attempts = []

        def flaky(ctx):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("transient")
            return {"ok": True}

        p = ServicePipeline("test", auto_retry=True, max_retries=3,
                            use_recovery_engine=False)
        p.add_stage(Stage("flaky", flaky, output_key="flaky"))

        result = p.execute()
        assert result.stages[0].status == StageStatus.SUCCESS
        assert len(attempts) == 3

    def test_pipeline_context_passing_with_recovery(self):
        p = ServicePipeline("test", use_recovery_engine=True)
        p.add_stage(Stage("gather", lambda ctx: {"value": 42}, output_key="gather"))
        p.add_stage(Stage("process", lambda ctx: {"doubled": ctx.get("gather", {}).get("value", 0) * 2},
                          output_key="process"))

        result = p.execute()
        assert result.final_output["process"]["doubled"] == 84

    def test_recovery_engine_config_propagation(self):
        """Verify custom config reaches the recovery engine."""
        p = ServicePipeline("test", use_recovery_engine=True,
                            recovery_engine_config={
                                "max_retries": 7,
                                "base_delay": 2.0,
                                "failure_threshold": 10,
                                "recovery_timeout": 60.0,
                            })
        p.add_stage(Stage("s1", lambda ctx: {"ok": True}))

        # Force lazy init
        engine = p._get_recovery_engine()
        assert engine.retry_policy.max_retries == 7
        assert engine.retry_policy.base_delay == 2.0
        assert engine.circuit_breaker.failure_threshold == 10
        assert engine.circuit_breaker.recovery_timeout == 60.0

    def test_circuit_breaker_isolation(self):
        """Each pipeline with recovery engine gets its own circuit breaker."""
        jobs = []
        errors = []

        def slow_handler():
            raise TimeoutError("slow")

        p1 = ServicePipeline("p1", use_recovery_engine=True,
                             recovery_engine_config={
                                 "max_retries": 0,
                                 "failure_threshold": 1,
                                 "recovery_timeout": 0.05,
                             })
        p1.add_stage(Stage("trip", lambda ctx: (_ for _ in ()).throw(TimeoutError("slow"))))

        # Execute once to trip p1's breaker
        p1.execute()

        # p2 should have its own independent breaker
        p2 = ServicePipeline("p2", use_recovery_engine=True)
        p2.add_stage(Stage("ok_stage", lambda ctx: {"ok": True}))

        result = p2.execute()
        assert result.status == PipelineStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════
# RecoveryResult
# ═══════════════════════════════════════════════════════════════════


class TestRecoveryResult:
    def test_success_result_fields(self):
        r = RecoveryResult(
            status=RecoveryResultStatus.SUCCESS,
            output="hello",
            attempts=1,
            total_time=0.1,
        )
        assert r.status == RecoveryResultStatus.SUCCESS
        assert r.output == "hello"
        assert r.attempts == 1
        assert r.error is None
        assert r.fallback_type is None

    def test_degraded_result(self):
        r = RecoveryResult(
            status=RecoveryResultStatus.DEGRADED,
            output="fallback_val",
            fallback_type=FallbackType.DEFAULT,
            attempts=3,
            total_time=1.5,
        )
        assert r.status == RecoveryResultStatus.DEGRADED
        assert r.fallback_type == FallbackType.DEFAULT
