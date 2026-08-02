"""
tests/test_cost_per_success.py — tests for CostPerSuccessTracker

Coverage:
- TaskOutcomeRecord creation
- CostPerSuccessTracker.record() and basic metrics
- cost_per_successful_run / success_rate calculations
- avg_tokens_per_run
- cost_trend (day/hour buckets)
- CostEfficiencyAlert (2× baseline trigger)
- get_report() in json and markdown format
- Persistence round-trip
- Reset
- API endpoint GET /api/dashboard/cost-efficiency
- Edge cases (empty tracker, zero cost, all failures)
"""

import json
import os
import tempfile
import time

import pytest
from starlette.testclient import TestClient

from jarvis.cost_per_success import (
    CostPerSuccessTracker,
    CostEfficiencyAlert,
    TaskOutcomeRecord,
)


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def tracker():
    """Fresh CostPerSuccessTracker — no persistence, low baseline for alert tests."""
    t = CostPerSuccessTracker(baseline_cost_per_success=0.01)
    yield t
    t.reset()


@pytest.fixture
def populated_tracker():
    """Tracker with 10 mixed-success records."""
    t = CostPerSuccessTracker(baseline_cost_per_success=0.05)
    # 7 successes, 3 failures
    t.record("task-1", success=True, cost_usd=0.01, tokens_in=500, tokens_out=200,
             execution_time_ms=300, domain="general", model_calls=2)
    t.record("task-2", success=True, cost_usd=0.02, tokens_in=800, tokens_out=400,
             execution_time_ms=500, domain="code", model_calls=3)
    t.record("task-3", success=False, cost_usd=0.005, tokens_in=300, tokens_out=0,
             execution_time_ms=150, domain="general", model_calls=1)
    t.record("task-4", success=True, cost_usd=0.015, tokens_in=600, tokens_out=300,
             execution_time_ms=400, domain="qa", model_calls=2)
    t.record("task-5", success=True, cost_usd=0.012, tokens_in=450, tokens_out=250,
             execution_time_ms=350, domain="general", model_calls=2)
    t.record("task-6", success=False, cost_usd=0.008, tokens_in=400, tokens_out=0,
             execution_time_ms=200, domain="code", model_calls=1)
    t.record("task-7", success=True, cost_usd=0.025, tokens_in=1000, tokens_out=500,
             execution_time_ms=600, domain="qa", model_calls=4)
    t.record("task-8", success=True, cost_usd=0.018, tokens_in=700, tokens_out=350,
             execution_time_ms=450, domain="general", model_calls=2)
    t.record("task-9", success=False, cost_usd=0.003, tokens_in=200, tokens_out=0,
             execution_time_ms=100, domain="general", model_calls=1)
    t.record("task-10", success=True, cost_usd=0.022, tokens_in=900, tokens_out=450,
             execution_time_ms=520, domain="code", model_calls=3)
    yield t
    t.reset()


# ══════════════════════════════════════════════════════════════════
# Test 1: TaskOutcomeRecord
# ══════════════════════════════════════════════════════════════════

class TestTaskOutcomeRecord:

    def test_create_record(self):
        """TaskOutcomeRecord should store all fields correctly."""
        ts = time.time()
        r = TaskOutcomeRecord(
            timestamp=ts,
            task_id="abc123",
            success=True,
            cost_usd=0.05,
            tokens_in=500,
            tokens_out=200,
            execution_time_ms=350.0,
            domain="general",
            model_calls=2,
        )
        assert r.task_id == "abc123"
        assert r.success is True
        assert r.cost_usd == 0.05
        assert r.tokens_in == 500
        assert r.tokens_out == 200
        assert r.execution_time_ms == 350.0
        assert r.domain == "general"
        assert r.model_calls == 2


# ══════════════════════════════════════════════════════════════════
# Test 2: Basic metrics
# ══════════════════════════════════════════════════════════════════

class TestBasicMetrics:

    def test_record_and_counts(self, tracker):
        """Recording tasks should update total/success/fail counts."""
        assert tracker.total_tasks() == 0
        tracker.record("t1", success=True, cost_usd=0.01)
        tracker.record("t2", success=False, cost_usd=0.005)
        assert tracker.total_tasks() == 2
        assert tracker.success_count() == 1
        assert tracker.fail_count() == 1

    def test_total_cost(self, populated_tracker):
        """total_cost should match sum of all individual costs."""
        total = populated_tracker.total_cost()
        expected = 0.01 + 0.02 + 0.005 + 0.015 + 0.012 + 0.008 + 0.025 + 0.018 + 0.003 + 0.022
        assert abs(total - expected) < 0.0001

    def test_cost_per_successful_run(self, populated_tracker):
        """CPSR = total_cost / successful_tasks."""
        cpsr = populated_tracker.cost_per_successful_run()
        total_cost = populated_tracker.total_cost()
        success = populated_tracker.success_count()
        assert success == 7
        assert abs(cpsr - total_cost / success) < 0.0001

    def test_success_rate(self, populated_tracker):
        """success_rate = successful / total."""
        rate = populated_tracker.success_rate()
        assert rate == 0.7  # 7/10


# ══════════════════════════════════════════════════════════════════
# Test 3: avg_tokens_per_run
# ══════════════════════════════════════════════════════════════════

class TestAvgTokens:

    def test_avg_tokens_per_run(self, populated_tracker):
        """Should return average tokens in/out across all tasks."""
        avg = populated_tracker.avg_tokens_per_run()
        # tokens_in total: 500+800+300+600+450+400+1000+700+200+900 = 5850
        assert avg["avg_tokens_in"] == 585  # 5850 // 10
        # tokens_out total: 200+400+0+300+250+0+500+350+0+450 = 2450
        assert avg["avg_tokens_out"] == 245  # 2450 // 10

    def test_avg_tokens_empty(self, tracker):
        """Empty tracker should return zeros."""
        avg = tracker.avg_tokens_per_run()
        assert avg["avg_tokens_in"] == 0
        assert avg["avg_tokens_out"] == 0


# ══════════════════════════════════════════════════════════════════
# Test 4: Cost trend
# ══════════════════════════════════════════════════════════════════

class TestCostTrend:

    def test_cost_trend_day_buckets(self, tracker):
        """cost_trend('day') should bucket records by date."""
        tracker.record("t1", success=True, cost_usd=0.01, tokens_in=100, tokens_out=50)
        tracker.record("t2", success=True, cost_usd=0.02, tokens_in=200, tokens_out=100)
        trend = tracker.cost_trend(bucket="day", hours=24)
        assert len(trend) >= 1
        day = trend[0]
        assert "label" in day
        assert "cost_usd" in day
        assert "total_tasks" in day
        assert "successful_tasks" in day
        assert "cost_per_success" in day
        assert "success_rate" in day

    def test_cost_trend_hour_buckets(self, tracker):
        """cost_trend('hour') should bucket records by hour."""
        tracker.record("t1", success=True, cost_usd=0.01, tokens_in=100, tokens_out=50)
        trend = tracker.cost_trend(bucket="hour", hours=24)
        assert len(trend) >= 1
        assert ":00" in trend[0]["label"]

    def test_cost_trend_empty(self, tracker):
        """Empty tracker should return empty trend list."""
        trend = tracker.cost_trend(bucket="day", hours=7 * 24)
        assert trend == []


# ══════════════════════════════════════════════════════════════════
# Test 5: CostEfficiencyAlert
# ══════════════════════════════════════════════════════════════════

class TestCostEfficiencyAlert:

    def test_alert_triggered_above_2x(self, tracker):
        """When CPSR > 2× baseline, alert should trigger."""
        # baseline is 0.01; record tasks that push CPSR > 0.02
        for i in range(10):
            tracker.record(
                f"a-{i}", success=True, cost_usd=0.03,
                tokens_in=100, tokens_out=50,
            )
        # CPSR ≈ 0.03, baseline 0.01 → ratio ≈ 3×
        alert = tracker.last_alert
        assert alert is not None
        assert alert.ratio >= 2.0
        assert alert.to_dict()["severity"] in ("warning", "critical")

    def test_no_alert_below_2x(self, tracker):
        """When CPSR ≤ 2× baseline, no alert should fire."""
        for i in range(10):
            tracker.record(
                f"b-{i}", success=True, cost_usd=0.005,
                tokens_in=100, tokens_out=50,
            )
        # CPSR ≈ 0.005, baseline 0.01 → ratio 0.5×
        alert = tracker.last_alert
        assert alert is None

    def test_no_alert_with_few_records(self, tracker):
        """Less than 5 records should not trigger alerts."""
        for i in range(3):
            tracker.record(
                f"c-{i}", success=True, cost_usd=0.10,
                tokens_in=100, tokens_out=50,
            )
        alert = tracker.last_alert
        assert alert is None


# ══════════════════════════════════════════════════════════════════
# Test 6: get_report
# ══════════════════════════════════════════════════════════════════

class TestGetReport:

    def test_json_report_structure(self, populated_tracker):
        """get_report('json') should contain all expected keys."""
        report = populated_tracker.get_report(format="json")
        assert report["total_tasks"] == 10
        assert report["successful_tasks"] == 7
        assert report["failed_tasks"] == 3
        assert "success_rate" in report
        assert "total_cost_usd" in report
        assert "cost_per_successful_run" in report
        assert "avg_tokens_in" in report
        assert "avg_tokens_out" in report
        assert "baseline_cpsr" in report
        assert "deviation_from_baseline" in report
        assert "cost_trend" in report
        assert "active_alert" in report

    def test_markdown_report(self, populated_tracker):
        """get_report('markdown') should include a 'markdown' key."""
        report = populated_tracker.get_report(format="markdown")
        assert "markdown" in report
        md = report["markdown"]
        assert "Cost Efficiency Report" in md
        assert "Total Cost:" in md
        assert "Cost per Successful Run:" in md

    def test_report_with_hours(self, populated_tracker):
        """Windowed report should only cover specified hours."""
        report = populated_tracker.get_report(format="json", hours=24)
        assert report["window_hours"] == 24
        # All 10 records were just created, so should all be within 24h
        assert report["total_tasks"] == 10

    def test_report_empty(self, tracker):
        """Empty tracker report should have zeros."""
        report = tracker.get_report(format="json")
        assert report["total_tasks"] == 0
        assert report["successful_tasks"] == 0
        assert report["total_cost_usd"] == 0.0
        assert report["cost_per_successful_run"] == 0.0


# ══════════════════════════════════════════════════════════════════
# Test 7: Persistence
# ══════════════════════════════════════════════════════════════════

class TestPersistence:

    def test_persistence_roundtrip(self):
        """Records should survive save + reload."""
        tmp = os.path.join(
            tempfile.gettempdir(),
            f"test_cpsr_{int(time.time())}.json",
        )
        try:
            t1 = CostPerSuccessTracker(
                baseline_cost_per_success=0.05, persistence_path=tmp,
            )
            t1.record("p1", success=True, cost_usd=0.01, tokens_in=100, tokens_out=50)
            t1.record("p2", success=False, cost_usd=0.005, tokens_in=50, tokens_out=0)

            # Reload
            t2 = CostPerSuccessTracker(
                baseline_cost_per_success=0.05, persistence_path=tmp,
            )
            assert t2.total_tasks() == 2
            assert t2.success_count() == 1
            assert t2.fail_count() == 1
            assert t2.total_cost() > 0
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ══════════════════════════════════════════════════════════════════
# Test 8: Reset
# ══════════════════════════════════════════════════════════════════

class TestReset:

    def test_reset_clears_all(self, populated_tracker):
        """reset() should clear records and alerts."""
        assert populated_tracker.total_tasks() == 10
        populated_tracker.reset()
        assert populated_tracker.total_tasks() == 0
        assert populated_tracker.success_count() == 0
        assert populated_tracker.total_cost() == 0.0
        assert populated_tracker.cost_per_successful_run() == 0.0
        assert populated_tracker.last_alert is None


# ══════════════════════════════════════════════════════════════════
# Test 9: Edge cases
# ══════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_all_failures_zero_cpsr(self, tracker):
        """When all tasks fail, CPSR should be 0 (no successful runs)."""
        for i in range(5):
            tracker.record(f"f-{i}", success=False, cost_usd=0.01,
                           tokens_in=100, tokens_out=0)
        assert tracker.cost_per_successful_run() == 0.0
        assert tracker.success_rate() == 0.0

    def test_zero_cost(self, tracker):
        """Tasks with zero cost should not distort metrics."""
        tracker.record("z1", success=True, cost_usd=0.0,
                       tokens_in=0, tokens_out=0)
        assert tracker.cost_per_successful_run() == 0.0

    def test_single_record(self, tracker):
        """Single record should compute metrics correctly."""
        tracker.record("s1", success=True, cost_usd=0.03,
                       tokens_in=300, tokens_out=150,
                       execution_time_ms=200, domain="qa", model_calls=2)
        assert tracker.total_tasks() == 1
        assert tracker.success_rate() == 1.0
        assert tracker.cost_per_successful_run() == 0.03
        avg = tracker.avg_tokens_per_run()
        assert avg["avg_tokens_in"] == 300
        assert avg["avg_tokens_out"] == 150


# ══════════════════════════════════════════════════════════════════
# Test 10: API endpoint
# ══════════════════════════════════════════════════════════════════

class TestApiEndpoint:

    @pytest.fixture
    def client(self, populated_tracker):
        """Create a TestClient with the tracker in app.extra."""
        from jarvis.court_api import create_app
        app = create_app()
        app.extra["cost_per_success"] = populated_tracker
        return TestClient(app)

    def test_api_cost_efficiency(self, client):
        """GET /api/dashboard/cost-efficiency returns full report."""
        resp = client.get("/api/dashboard/cost-efficiency")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tasks"] == 10
        assert data["successful_tasks"] == 7
        assert "cost_per_successful_run" in data
        assert "cost_trend" in data

    def test_api_cost_efficiency_with_params(self, client):
        """GET /api/dashboard/cost-efficiency?hours=24&trend_bucket=hour."""
        resp = client.get(
            "/api/dashboard/cost-efficiency?hours=24&trend_bucket=hour"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["window_hours"] == 24

    def test_api_no_tracker(self):
        """API should return 503 when tracker not available."""
        from jarvis.court_api import create_app
        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/dashboard/cost-efficiency")
        assert resp.status_code == 503
