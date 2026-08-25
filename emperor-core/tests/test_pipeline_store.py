"""Tests for huanxin.pipeline_store — in-memory pipeline execution records."""

import pytest
from huanxin.pipeline_store import PipelineStore, pipeline_store, MAX_RECORDS


class TestPipelineStore:

    def setup_method(self):
        """Ensure a clean store before each test."""
        pipeline_store.clear()

    def test_add_and_retrieve_single(self):
        """Add one record and retrieve it by ID."""
        pipeline_store.add(
            template="daily_brief",
            pipeline_id="pipe-001",
            status="running",
            steps=3,
            total_steps=5,
            elapsed_ms=1500.0,
        )
        rec = pipeline_store.get_by_id("pipe-001")
        assert rec is not None
        assert rec["template"] == "daily_brief"
        assert rec["status"] == "running"
        assert rec["steps"] == 3
        assert rec["total_steps"] == 5
        assert rec["elapsed_ms"] == 1500.0

    def test_get_recent_newest_first(self):
        """get_recent() returns records newest-first."""
        pipeline_store.add("t1", "id-1", "completed", steps=1, total_steps=3, elapsed_ms=100)
        pipeline_store.add("t2", "id-2", "running", steps=2, total_steps=4, elapsed_ms=200)
        pipeline_store.add("t3", "id-3", "failed", steps=1, total_steps=2, elapsed_ms=50)

        recent = pipeline_store.get_recent(limit=10)
        assert len(recent) == 3
        # Newest first
        assert recent[0]["pipeline_id"] == "id-3"
        assert recent[1]["pipeline_id"] == "id-2"
        assert recent[2]["pipeline_id"] == "id-1"

    def test_get_recent_limit(self):
        """get_recent() respects the limit parameter."""
        for i in range(20):
            pipeline_store.add(f"t{i}", f"id-{i}", "completed")
        assert len(pipeline_store.get_recent(limit=3)) == 3
        assert len(pipeline_store.get_recent(limit=7)) == 7

    def test_get_recent_status_filter(self):
        """get_recent() filters by status when provided."""
        pipeline_store.add("t1", "id-1", "completed")
        pipeline_store.add("t2", "id-2", "running")
        pipeline_store.add("t3", "id-3", "failed")
        pipeline_store.add("t4", "id-4", "completed")

        completed = pipeline_store.get_recent(limit=10, status="completed")
        assert len(completed) == 2
        assert all(r["status"] == "completed" for r in completed)

        running = pipeline_store.get_recent(limit=10, status="running")
        assert len(running) == 1
        assert running[0]["pipeline_id"] == "id-2"

    def test_max_records_cap(self):
        """Store trims to MAX_RECORDS when exceeded."""
        for i in range(MAX_RECORDS + 50):
            pipeline_store.add(f"t{i}", f"id-{i}", "completed")
        assert pipeline_store.count == MAX_RECORDS
        # Oldest records should be evicted
        recent = pipeline_store.get_recent(limit=1)
        assert recent[0]["pipeline_id"] == f"id-{MAX_RECORDS + 49}"

    def test_update_existing_record(self):
        """update() modifies an existing record in place."""
        pipeline_store.add("t1", "pipe-001", "running", steps=1, total_steps=3, elapsed_ms=500)
        ok = pipeline_store.update(
            pipeline_id="pipe-001",
            status="completed",
            steps=3,
            elapsed_ms=1500.0,
            step_details=[
                {"step_name": "gather", "status": "success", "elapsed_ms": 300},
                {"step_name": "analyze", "status": "success", "elapsed_ms": 700},
                {"step_name": "report", "status": "success", "elapsed_ms": 500},
            ],
        )
        assert ok
        rec = pipeline_store.get_by_id("pipe-001")
        assert rec["status"] == "completed"
        assert rec["steps"] == 3
        assert rec["elapsed_ms"] == 1500.0
        assert len(rec["step_details"]) == 3
        assert rec["step_details"][0]["step_name"] == "gather"

    def test_update_nonexistent_record(self):
        """update() returns False for missing pipeline_id."""
        ok = pipeline_store.update("no-such-id", status="completed")
        assert not ok

    def test_get_by_id_missing(self):
        """get_by_id() returns None for unknown pipeline_id."""
        assert pipeline_store.get_by_id("nonexistent") is None
