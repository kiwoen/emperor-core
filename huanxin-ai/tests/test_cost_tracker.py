"""
tests/test_cost_tracker.py — 10 tests for CostTracker

Coverage:
- CostRecord creation and serialization
- CostTracker.record() / daily_total() / monthly_total() / all_time_total()
- per_model_breakdown()
- history()
- summary()
- JSON persistence round-trip
- Multi-model tracking
- Reset
- API endpoints (GET /api/costs/summary, /api/costs/history, /api/costs/by-model)
- Edge cases (empty tracker, no records)
"""

import json
import os
import tempfile
import time

import pytest
from starlette.testclient import TestClient

from huanxin.cost_tracker import CostRecord, CostTracker


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def tracker():
    """Fresh CostTracker — no persistence."""
    t = CostTracker()
    yield t
    t.reset()


@pytest.fixture
def populated_tracker():
    """CostTracker with 5 pre-loaded records across 3 models."""
    t = CostTracker()
    t.record("gpt-4o-mini", tokens_in=500, tokens_out=200, task_id="task-1", operation="invoke")
    t.record("gpt-4o-mini", tokens_in=300, tokens_out=150, task_id="task-2", operation="invoke")
    t.record("deepseek-chat", tokens_in=1000, tokens_out=800, task_id="task-3", operation="parallel")
    t.record("deepseek-chat", tokens_in=600, tokens_out=400, task_id="task-4", operation="parallel")
    t.record("claude-3-haiku", tokens_in=200, tokens_out=100, task_id="task-5", operation="invoke")
    yield t
    t.reset()


# ──────────────────────────────────────────────
# Test 1: CostRecord creation and serialization
# ──────────────────────────────────────────────

class TestCostRecord:

    def test_create_and_serialize(self):
        """CostRecord should be created with correct fields and serialize properly."""
        ts = time.time()
        record = CostRecord(
            timestamp=ts,
            model_name="gpt-4o",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.0003,
            task_id="test-1",
            operation="invoke",
        )
        d = record.to_dict()
        assert d["model_name"] == "gpt-4o"
        assert d["tokens_in"] == 100
        assert d["tokens_out"] == 50
        assert d["cost_usd"] == 0.0003
        assert d["task_id"] == "test-1"
        assert d["operation"] == "invoke"
        assert "timestamp_iso" in d
        assert abs(d["timestamp"] - ts) < 0.01

    def test_from_dict_roundtrip(self):
        """CostRecord.from_dict should reconstruct identical objects."""
        original = CostRecord(
            timestamp=1234567890.0,
            model_name="deepseek-chat",
            tokens_in=800,
            tokens_out=600,
            cost_usd=0.0028,
            task_id="roundtrip",
            operation="ensemble",
        )
        d = original.to_dict()
        restored = CostRecord.from_dict(d)
        assert restored.model_name == original.model_name
        assert restored.tokens_in == original.tokens_in
        assert restored.tokens_out == original.tokens_out
        assert restored.cost_usd == original.cost_usd
        assert restored.task_id == original.task_id
        assert restored.operation == original.operation
        assert restored.timestamp == original.timestamp


# ──────────────────────────────────────────────
# Test 2: Record and basic totals
# ──────────────────────────────────────────────

class TestBasicTotals:

    def test_record_and_daily_total(self, tracker):
        """Recording a call should increase daily_total."""
        assert tracker.daily_total() == 0.0
        tracker.record("gpt-4o-mini", tokens_in=500, tokens_out=200)
        assert tracker.daily_total() > 0.0

    def test_monthly_total_present(self, populated_tracker):
        """monthly_total should return non-zero after records."""
        assert populated_tracker.monthly_total() > 0.0

    def test_all_time_total(self, populated_tracker):
        """all_time_total should equal sum of all recorded costs."""
        total = populated_tracker.all_time_total()
        expected = sum(
            r.cost_usd for r in populated_tracker._records_snapshot()
        )
        assert abs(total - expected) < 0.0001


# ──────────────────────────────────────────────
# Test 3: per_model_breakdown
# ──────────────────────────────────────────────

class TestPerModelBreakdown:

    def test_per_model_breakdown(self, populated_tracker):
        """per_model_breakdown should group by model_name."""
        breakdown = populated_tracker.per_model_breakdown()
        assert "gpt-4o-mini" in breakdown
        assert "deepseek-chat" in breakdown
        assert "claude-3-haiku" in breakdown

        gpt = breakdown["gpt-4o-mini"]
        assert gpt["calls"] == 2
        assert gpt["tokens_in"] == 800   # 500 + 300
        assert gpt["tokens_out"] == 350  # 200 + 150

        deepseek = breakdown["deepseek-chat"]
        assert deepseek["calls"] == 2
        assert deepseek["tokens_in"] == 1600   # 1000 + 600
        assert deepseek["tokens_out"] == 1200  # 800 + 400

        claude = breakdown["claude-3-haiku"]
        assert claude["calls"] == 1

    def test_per_model_breakdown_empty(self, tracker):
        """Empty tracker should return empty dict."""
        breakdown = tracker.per_model_breakdown()
        assert breakdown == {}


# ──────────────────────────────────────────────
# Test 4: history
# ──────────────────────────────────────────────

class TestHistory:

    def test_history_returns_newest_first(self, populated_tracker):
        """history() should return records sorted newest-first."""
        records = populated_tracker.history(limit=50)
        assert len(records) == 5
        # Verify descending timestamp order
        timestamps = [r["timestamp"] for r in records]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_history_limit(self, populated_tracker):
        """history(limit=3) should return at most 3 records."""
        records = populated_tracker.history(limit=3)
        assert len(records) == 3

    def test_history_empty(self, tracker):
        """Empty tracker should return empty list."""
        records = tracker.history()
        assert records == []


# ──────────────────────────────────────────────
# Test 5: summary
# ──────────────────────────────────────────────

class TestSummary:

    def test_summary_structure(self, populated_tracker):
        """summary() should return all expected keys."""
        s = populated_tracker.summary()
        assert "today_usd" in s
        assert "this_month_usd" in s
        assert "all_time_usd" in s
        assert "total_calls" in s
        assert "by_model_today" in s
        assert "by_model_month" in s
        assert s["total_calls"] == 5

    def test_summary_empty(self, tracker):
        """Empty tracker summary should have zeros."""
        s = tracker.summary()
        assert s["today_usd"] == 0.0
        assert s["this_month_usd"] == 0.0
        assert s["all_time_usd"] == 0.0
        assert s["total_calls"] == 0


# ──────────────────────────────────────────────
# Test 6: JSON persistence round-trip
# ──────────────────────────────────────────────

class TestPersistence:

    def test_save_and_load(self):
        """CostTracker should persist records to JSON and reload them."""
        tmp = os.path.join(tempfile.gettempdir(), f"test_cost_{int(time.time())}.json")
        try:
            # Create and populate
            t1 = CostTracker(persistence_path=tmp)
            t1.record("gpt-4o", tokens_in=100, tokens_out=50, task_id="persist-1")
            t1.record("gpt-4o", tokens_in=200, tokens_out=100, task_id="persist-2")

            # Reload
            t2 = CostTracker(persistence_path=tmp)
            assert t2.all_time_total() > 0.0
            assert len(t2._records_snapshot()) == 2
            assert t2._records_snapshot()[0].task_id == "persist-1"
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ──────────────────────────────────────────────
# Test 7: Reset
# ──────────────────────────────────────────────

class TestReset:

    def test_reset_clears_all(self, populated_tracker):
        """reset() should clear all records and zero out totals."""
        assert populated_tracker.all_time_total() > 0.0
        populated_tracker.reset()
        assert populated_tracker.all_time_total() == 0.0
        assert populated_tracker.daily_total() == 0.0
        assert populated_tracker.monthly_total() == 0.0
        assert populated_tracker.history() == []
        assert populated_tracker.summary()["total_calls"] == 0


# ──────────────────────────────────────────────
# Test 8: API endpoints — summary
# ──────────────────────────────────────────────

class TestApiEndpoints:

    @pytest.fixture
    def client(self, populated_tracker):
        """Create a TestClient with the cost_tracker in app.extra."""
        from huanxin.court_api import create_app
        app = create_app()
        app.extra["cost_tracker"] = populated_tracker
        return TestClient(app)

    def test_api_summary(self, client):
        """GET /api/costs/summary returns cost breakdown."""
        resp = client.get("/api/costs/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 5
        assert data["today_usd"] >= 0

    def test_api_history(self, client):
        """GET /api/costs/history returns records."""
        resp = client.get("/api/costs/history?limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["records"]) == 3
        for r in data["records"]:
            assert "model_name" in r
            assert "cost_usd" in r

    def test_api_by_model(self, client):
        """GET /api/costs/by-model returns per-model breakdown."""
        resp = client.get("/api/costs/by-model")
        assert resp.status_code == 200
        data = resp.json()
        assert "today" in data
        assert "this_month" in data
        month = data["this_month"]
        assert "gpt-4o-mini" in month
        assert "deepseek-chat" in month

    def test_api_summary_no_tracker(self):
        """API should return 503 when cost_tracker not available."""
        from huanxin.court_api import create_app
        app = create_app()
        # Don't inject cost_tracker
        client = TestClient(app)
        resp = client.get("/api/costs/summary")
        assert resp.status_code == 503
