"""Tests for jarvis.loop_guard — AgentLoopGuard, CostCap, LoopDetector."""

from __future__ import annotations

import threading
import time

import pytest

from jarvis.loop_guard import (
    AgentLoopGuard,
    BudgetExceededError,
    CostCap,
    InfiniteLoopError,
    LoopDetector,
    LoopLimitExceededError,
    _TaskLoopState,
)

TEST_TASK = "test-task-001"


# ══════════════════════════════════════════════════════════════════
# Test 1: Normal completion — no exceptions within limits
# ══════════════════════════════════════════════════════════════════

class TestNormalCompletion:
    def test_guard_completes_within_limits(self):
        """Guard should not raise when iterations, cost, and actions are within limits."""
        guard = AgentLoopGuard(max_iterations=20, max_cost_per_run=5.00)
        for i in range(5):
            guard.guard_step(
                TEST_TASK,
                action=f"step_{i}",
                result=f"result_{i}",
                step_cost=0.01,
            )
        info = guard.task_info(TEST_TASK)
        assert info["iteration_count"] == 5
        assert info["accumulated_cost"] == pytest.approx(0.05, rel=0.01)

    def test_multiple_tasks_independent(self):
        """Each task should have independent tracking."""
        guard = AgentLoopGuard(max_iterations=20)
        guard.guard_step("task-a", action="a1", result="r1", step_cost=0.01)
        guard.guard_step("task-a", action="a2", result="r2", step_cost=0.01)
        guard.guard_step("task-b", action="b1", result="r3", step_cost=0.02)
        assert guard.task_info("task-a")["iteration_count"] == 2
        assert guard.task_info("task-b")["iteration_count"] == 1
        assert guard.task_info("task-a")["accumulated_cost"] == pytest.approx(0.02, rel=0.01)
        assert guard.task_info("task-b")["accumulated_cost"] == pytest.approx(0.02, rel=0.01)


# ══════════════════════════════════════════════════════════════════
# Test 2: Max iteration exceeded
# ══════════════════════════════════════════════════════════════════

class TestMaxIterations:
    def test_limit_exceeded_raises(self):
        """Exceeding max_iterations should raise LoopLimitExceededError."""
        guard = AgentLoopGuard(max_iterations=3)
        # Use different results to avoid dead-loop detection
        guard.guard_step(TEST_TASK, action="a", result="r1", step_cost=0.01)
        guard.guard_step(TEST_TASK, action="b", result="r2", step_cost=0.01)
        guard.guard_step(TEST_TASK, action="c", result="r3", step_cost=0.01)
        with pytest.raises(LoopLimitExceededError) as exc:
            guard.guard_step(TEST_TASK, action="d", result="r4", step_cost=0.01)
        assert TEST_TASK in str(exc.value)
        assert "4" in str(exc.value)  # iteration 4 > 3

    def test_exactly_at_limit_ok(self):
        """Exactly at max_iterations should be OK (no raise)."""
        guard = AgentLoopGuard(max_iterations=5)
        for i in range(5):
            guard.guard_step(TEST_TASK, action=f"step_{i}", result=f"res_{i}")
        assert guard.task_info(TEST_TASK)["iteration_count"] == 5

    def test_reset_task_clears_count(self):
        """reset_task should clear iteration count."""
        guard = AgentLoopGuard(max_iterations=3)
        for _ in range(2):
            guard.check_iteration(TEST_TASK)
        guard.reset_task(TEST_TASK)
        # After reset, can run 3 more
        for _ in range(3):
            guard.check_iteration(TEST_TASK)
        assert guard.task_info(TEST_TASK)["iteration_count"] == 3


# ══════════════════════════════════════════════════════════════════
# Test 3: Cost cap exceeded
# ══════════════════════════════════════════════════════════════════

class TestCostCap:
    def test_cost_cap_exceeded_raises(self):
        """Accumulated cost > max_cost_per_run should raise BudgetExceededError."""
        guard = AgentLoopGuard(max_cost_per_run=1.00)
        guard.guard_step(TEST_TASK, action="a", result="r1", step_cost=0.40)
        guard.guard_step(TEST_TASK, action="b", result="r2", step_cost=0.40)
        # 0.40+0.40+0.40 = 1.20 > 1.00 — third step triggers
        with pytest.raises(BudgetExceededError) as exc:
            guard.guard_step(TEST_TASK, action="c", result="r3", step_cost=0.40)
        assert TEST_TASK in str(exc.value)
        assert "1.20" in str(exc.value)

    def test_cost_cap_not_exceeded(self):
        """Cost below cap should not raise."""
        guard = AgentLoopGuard(max_cost_per_run=5.00)
        guard.guard_step(TEST_TASK, action="a", result="r1", step_cost=2.00)
        guard.guard_step(TEST_TASK, action="b", result="r2", step_cost=2.00)
        # 4.00 < 5.00 — no error
        assert guard.task_info(TEST_TASK)["accumulated_cost"] == pytest.approx(4.00, rel=0.01)

    def test_cost_cap_standalone(self):
        """CostCap standalone check works."""
        cap = CostCap(max_cost_per_run=3.00)
        cap.check(TEST_TASK, 2.99)  # OK
        with pytest.raises(BudgetExceededError):
            cap.check(TEST_TASK, 3.01)


# ══════════════════════════════════════════════════════════════════
# Test 4: Infinite loop detection
# ══════════════════════════════════════════════════════════════════

class TestLoopDetection:
    def test_dead_loop_detected(self):
        """3 consecutive same action + same result should raise InfiniteLoopError."""
        guard = AgentLoopGuard(max_loop_streak=3)
        guard.guard_step(TEST_TASK, action="search", result="no results found")
        guard.guard_step(TEST_TASK, action="search", result="no results found")
        # 3rd consecutive same triggers detection
        with pytest.raises(InfiniteLoopError) as exc:
            guard.guard_step(TEST_TASK, action="search", result="no results found")
        assert "search" in str(exc.value)
        assert "3" in str(exc.value)

    def test_same_action_different_result_resets(self):
        """Same action but different result should reset streak."""
        guard = AgentLoopGuard(max_loop_streak=3)
        guard.guard_step(TEST_TASK, action="search", result="result A")
        guard.guard_step(TEST_TASK, action="search", result="result B")
        guard.guard_step(TEST_TASK, action="search", result="result A")
        # All different results — should not raise
        guard.guard_step(TEST_TASK, action="search", result="result C")
        assert guard.task_info(TEST_TASK)["iteration_count"] == 4

    def test_different_actions_reset_streak(self):
        """Different actions should reset the streak."""
        guard = AgentLoopGuard(max_loop_streak=3)
        guard.guard_step(TEST_TASK, action="search", result="same")
        guard.guard_step(TEST_TASK, action="search", result="same")
        guard.guard_step(TEST_TASK, action="llm_call", result="same")  # different action → reset
        guard.guard_step(TEST_TASK, action="search", result="same")  # streak=1
        guard.guard_step(TEST_TASK, action="search", result="same")  # streak=2
        # 3rd consecutive after reset triggers detection
        with pytest.raises(InfiniteLoopError):
            guard.guard_step(TEST_TASK, action="search", result="same")  # streak=3

    def test_loop_detector_standalone(self):
        """LoopDetector standalone check works — raises on 3rd same."""
        detector = LoopDetector(max_streak=3)
        state = _TaskLoopState()
        detector.check(state, "act", "result")  # streak=1
        detector.check(state, "act", "result")  # streak=2
        # 3rd same triggers
        with pytest.raises(InfiniteLoopError):
            detector.check(state, "act", "result")  # streak=3 >= max_streak=3


# ══════════════════════════════════════════════════════════════════
# Test 5: Exception properties
# ══════════════════════════════════════════════════════════════════

class TestExceptionProperties:
    def test_budget_exceeded_error(self):
        """BudgetExceededError carries task_id, accumulated, cap."""
        exc = BudgetExceededError("t1", 5.50, 5.00)
        assert exc.task_id == "t1"
        assert exc.accumulated == 5.50
        assert exc.cap == 5.00
        assert "t1" in str(exc)

    def test_loop_limit_exceeded_error(self):
        """LoopLimitExceededError carries task_id, iterations, max."""
        exc = LoopLimitExceededError("t2", 21, 20)
        assert exc.task_id == "t2"
        assert exc.iterations == 21
        assert exc.max_iterations == 20

    def test_infinite_loop_error(self):
        """InfiniteLoopError carries task_id, action, streak."""
        exc = InfiniteLoopError("t3", "search", 3)
        assert exc.task_id == "t3"
        assert exc.action == "search"
        assert exc.streak == 3


# ══════════════════════════════════════════════════════════════════
# Test 6: Thread safety
# ══════════════════════════════════════════════════════════════════

class TestThreadSafety:
    def test_concurrent_tasks_no_race(self):
        """Multiple tasks from different threads should not corrupt state."""
        guard = AgentLoopGuard(max_iterations=100, max_cost_per_run=100.00)
        errors = []

        def run_task(tid):
            try:
                for i in range(10):
                    guard.guard_step(
                        tid, action=f"step_{i}", result=f"res_{i}", step_cost=0.001
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=run_task, args=(f"task-{i}",))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert guard.active_tasks == 10
        for i in range(10):
            assert guard.task_info(f"task-{i}")["iteration_count"] == 10


# ══════════════════════════════════════════════════════════════════
# Test 7: Integration guard_step full cycle
# ══════════════════════════════════════════════════════════════════

class TestGuardStepIntegration:
    def test_all_three_checks_in_guard_step(self):
        """guard_step should invoke all three checks."""
        guard = AgentLoopGuard(max_iterations=5, max_cost_per_run=1.00, max_loop_streak=3)
        # Normal usage
        guard.guard_step("gsi", action="a", result="r1", step_cost=0.10)
        guard.guard_step("gsi", action="b", result="r2", step_cost=0.10)
        info = guard.task_info("gsi")
        assert info["iteration_count"] == 2
        assert info["accumulated_cost"] == pytest.approx(0.20, rel=0.01)

    def test_guard_step_triggers_limit(self):
        """guard_step should trigger LoopLimitExceededError via check_iteration."""
        guard = AgentLoopGuard(max_iterations=2)
        guard.guard_step("gsl", action="a", result="r1", step_cost=0.01)
        guard.guard_step("gsl", action="b", result="r2", step_cost=0.01)
        with pytest.raises(LoopLimitExceededError):
            guard.guard_step("gsl", action="c", result="r3", step_cost=0.01)

    def test_guard_step_triggers_cost(self):
        """guard_step should trigger BudgetExceededError via check_cost."""
        guard = AgentLoopGuard(max_cost_per_run=0.50)
        guard.guard_step("gsc", action="a", result="r1", step_cost=0.30)
        with pytest.raises(BudgetExceededError):
            guard.guard_step("gsc", action="b", result="r2", step_cost=0.30)
