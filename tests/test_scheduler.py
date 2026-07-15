"""Tests for jarvis.court.scheduler — Scheduler lifecycle, job management,
periodic evolution + task scheduling.
"""

from __future__ import annotations

import threading
import time
import pytest
from unittest.mock import MagicMock, patch

from jarvis.court.scheduler import (
    Scheduler,
    SchedulerState,
    ScheduleEntry,
    SchedulerReport,
)


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def scheduler():
    return Scheduler()


@pytest.fixture
def emperor_scheduler():
    emp = MagicMock()
    emp.evolve.return_value = {"cycles": 3, "ministers": 10}
    emp.execute_batch.return_value = [{"task_id": "x", "success": True}]
    return Scheduler(emp)


# ══════════════════════════════════════════════════════════════════
# State transitions
# ══════════════════════════════════════════════════════════════════


class TestLifecycle:
    def test_initial_state(self, scheduler):
        assert scheduler.state == SchedulerState.IDLE
        r = scheduler.report()
        assert r.state == "IDLE"
        assert r.entries == []
        assert r.total_runs == 0
        assert r.total_failures == 0

    def test_start_stop(self, scheduler):
        scheduler.add_job("ping", lambda: None, 0.1)
        scheduler.start()
        assert scheduler.state == SchedulerState.RUNNING
        time.sleep(0.5)  # let a few ticks pass
        scheduler.stop(timeout=1)
        # Should be STOPPED or STOPPING (if thread still joining)
        assert scheduler.state in (SchedulerState.STOPPED, SchedulerState.STOPPING)

    def test_start_idempotent(self, scheduler):
        scheduler.start()
        scheduler.start()  # second call — should not crash
        scheduler.stop()

    def test_pause_resume(self, scheduler):
        counter = [0]

        def inc():
            counter[0] += 1

        scheduler.add_job("inc", inc, 0.05)
        scheduler.start()
        time.sleep(0.25)
        before_pause = counter[0]
        assert before_pause >= 1

        scheduler.pause()
        assert scheduler.state == SchedulerState.PAUSED
        snapshot = counter[0]
        time.sleep(0.2)  # should NOT increment during pause
        assert counter[0] == snapshot

        scheduler.resume()
        assert scheduler.state == SchedulerState.RUNNING
        time.sleep(0.25)
        assert counter[0] > snapshot

        scheduler.stop()

    def test_stop_from_idle(self, scheduler):
        scheduler.stop()  # should not crash
        assert scheduler.state == SchedulerState.IDLE


# ══════════════════════════════════════════════════════════════════
# Job management
# ══════════════════════════════════════════════════════════════════


class TestJobManagement:
    def test_add_and_list(self, scheduler):
        scheduler.add_job("a", lambda: 1, 10)
        scheduler.add_job("b", lambda: 2, 20, tags=["critical"])

        jobs = scheduler.list_jobs()
        assert len(jobs) == 2
        names = {j.name for j in jobs}
        assert names == {"a", "b"}

    def test_add_overwrites(self, scheduler):
        results = [0]

        def first():
            results[0] = 1

        def second():
            results[0] = 2

        scheduler.add_job("x", first, 10)
        scheduler.add_job("x", second, 10)
        # Fire the job manually to verify overwrite
        entry = scheduler.get_job("x")
        entry.action()
        assert results[0] == 2

    def test_add_invalid_interval(self, scheduler):
        with pytest.raises(ValueError, match="must be > 0"):
            scheduler.add_job("bad", lambda: None, 0)

        with pytest.raises(ValueError, match="must be > 0"):
            scheduler.add_job("bad", lambda: None, -5)

    def test_remove_job(self, scheduler):
        scheduler.add_job("j", lambda: None, 10)
        assert scheduler.remove_job("j") is True
        assert scheduler.remove_job("j") is False  # idempotent
        assert len(scheduler.list_jobs()) == 0

    def test_enable_disable(self, scheduler):
        scheduler.add_job("j", lambda: None, 10, enabled=False)
        j = scheduler.get_job("j")
        assert j.enabled is False

        assert scheduler.enable_job("j") is True
        j = scheduler.get_job("j")
        assert j.enabled is True

        assert scheduler.disable_job("j") is True
        j = scheduler.get_job("j")
        assert j.enabled is False

        # Non-existent
        assert scheduler.enable_job("ghost") is False
        assert scheduler.disable_job("ghost") is False

    def test_get_job_returns_copy(self, scheduler):
        scheduler.add_job("orig", lambda: 1, 10)
        copy = scheduler.get_job("orig")
        copy.name = "hacked"
        # Original unchanged
        orig = scheduler.get_job("orig")
        assert orig.name == "orig"

    def test_list_jobs_returns_copies(self, scheduler):
        scheduler.add_job("a", lambda: 1, 10)
        jobs = scheduler.list_jobs()
        jobs[0].name = "hacked"
        orig = scheduler.get_job("a")
        assert orig.name == "a"


# ══════════════════════════════════════════════════════════════════
# Execution behavior
# ══════════════════════════════════════════════════════════════════


class TestExecution:
    def test_job_fires_repeatedly(self, scheduler):
        counter = [0]

        def inc():
            counter[0] += 1

        scheduler.add_job("inc", inc, 0.05)
        scheduler.start()
        time.sleep(1.0)
        scheduler.stop()
        # Should fire ~20 times — be lenient for CI timing
        assert counter[0] >= 2, f"Expected >=2 fires, got {counter[0]}"

    def test_job_failure_is_counted(self, scheduler):
        def doomed():
            raise ValueError("boom")

        scheduler.add_job("doomed", doomed, 0.1)
        scheduler.start()
        time.sleep(0.3)
        scheduler.stop()
        r = scheduler.report()
        assert r.total_failures >= 1
        e = r.entries[0]
        assert e["run_count"] >= 1

    def test_disabled_job_does_not_fire(self, scheduler):
        counter = [0]

        def inc():
            counter[0] += 1

        scheduler.add_job("inc", inc, 0.05, enabled=False)
        scheduler.start()
        time.sleep(0.3)
        scheduler.stop()
        assert counter[0] == 0

    def test_zero_interval_rejected(self, scheduler):
        with pytest.raises(ValueError):
            scheduler.add_job("z", lambda: None, 0)

    def test_negative_interval_rejected(self, scheduler):
        with pytest.raises(ValueError):
            scheduler.add_job("z", lambda: None, -5)


# ══════════════════════════════════════════════════════════════════
# Emperor integration
# ══════════════════════════════════════════════════════════════════


class TestEmperorIntegration:
    def test_schedule_evolution(self, emperor_scheduler):
        name = emperor_scheduler.schedule_evolution(interval_minutes=1, cycles=5)
        assert name == "_auto_evolution"
        j = emperor_scheduler.get_job(name)
        assert j is not None
        assert j.interval_seconds == 60.0
        assert j.enabled is True

    def test_schedule_evolution_fires_emperor(self, emperor_scheduler):
        emperor_scheduler.schedule_evolution(interval_minutes=0.01, cycles=3)
        emperor_scheduler.start()
        time.sleep(1.5)
        emperor_scheduler.stop()
        # emperor.evolve should have been called at least once
        assert emperor_scheduler._emperor.evolve.call_count >= 1

    def test_schedule_tasks(self, emperor_scheduler):
        templates = [
            {"prompt": "Hello", "domain": "chat"},
            {"prompt": "What is 3+4?", "domain": "math"},
        ]
        name = emperor_scheduler.schedule_tasks(
            interval_minutes=1, templates=templates,
        )
        assert name == "_auto_tasks"
        j = emperor_scheduler.get_job(name)
        assert j is not None
        assert j.interval_seconds == 60.0

    def test_schedule_tasks_fires_emperor(self, emperor_scheduler):
        emperor_scheduler.schedule_tasks(
            interval_minutes=0.01,
            templates=[{"prompt": "test"}],
        )
        emperor_scheduler.start()
        time.sleep(1.5)
        emperor_scheduler.stop()
        assert emperor_scheduler._emperor.execute_batch.call_count >= 1

    def test_no_emperor_convenience_raises(self, scheduler):
        with pytest.raises(RuntimeError, match="no Emperor"):
            scheduler.schedule_evolution(1)
        with pytest.raises(RuntimeError, match="no Emperor"):
            scheduler.schedule_tasks(1)


# ══════════════════════════════════════════════════════════════════
# Report accuracy
# ══════════════════════════════════════════════════════════════════


class TestReport:
    def test_report_after_runs(self, scheduler):
        scheduler.add_job("fast", lambda: None, 0.05)
        scheduler.start()
        time.sleep(1.0)
        scheduler.stop()
        r = scheduler.report()
        assert r.total_runs >= 2, f"Expected >=2 runs, got {r.total_runs}"
        assert r.total_failures == 0
        assert r.entries[0]["run_count"] == r.total_runs

    def test_report_includes_tags(self, scheduler):
        scheduler.add_job("t", lambda: None, 10, tags=["critical", "daily"])
        r = scheduler.report()
        assert r.entries[0]["tags"] == ["critical", "daily"]

    def test_report_idle(self, scheduler):
        r = scheduler.report()
        assert r.state == "IDLE"
        assert r.running_since == 0.0
        assert r.total_runs == 0

    def test_report_running_state(self, scheduler):
        scheduler.add_job("slow", lambda: None, 999)
        scheduler.start()
        try:
            r = scheduler.report()
            assert r.state == "RUNNING"
            assert r.running_since > 0
        finally:
            scheduler.stop()


# ══════════════════════════════════════════════════════════════════
# Thread safety (smoke tests)
# ══════════════════════════════════════════════════════════════════


class TestThreadSafety:
    def test_concurrent_add_jobs(self, scheduler):
        """Multiple threads adding jobs simultaneously."""
        errors = []

        def add_n(n):
            for i in range(20):
                try:
                    scheduler.add_job(f"job_{n}_{i}", lambda: n, 10)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=add_n, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(scheduler.list_jobs()) == 100

    def test_start_stop_many(self, scheduler):
        """Rapid start/stop cycles should not crash."""
        scheduler.add_job("ping", lambda: None, 0.05)
        for _ in range(10):
            scheduler.start()
            time.sleep(0.15)
            scheduler.stop(timeout=2)
        assert scheduler.state in (SchedulerState.STOPPED, SchedulerState.STOPPING)
