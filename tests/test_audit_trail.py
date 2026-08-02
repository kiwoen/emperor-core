"""
Tests for jarvis.tools.audit_trail — AuditTrail, AuditReplayer, and integration.

Covers:
  1. record() writes a record and returns an id
  2. Query APIs: by_tool_name, by_agent, by_time_range, by_task, get_failed, get_slow
  3. record_from_log() from ToolCallLog
  4. AuditReplayer.replay_trace() produces Markdown table
  5. archive_old() archives records older than threshold
  6. Concurrent writes from multiple threads
  7. Integration: safe_execute with audit_trail records audit entries
  8. _safe_json_dumps handles non-serializable objects gracefully
  9. count() returns correct record count
  10. get_recent() returns most recent records limited correctly
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from jarvis.tools.audit_trail import (
    AuditTrail,
    AuditRecord,
    AuditReplayer,
    _safe_json_dumps,
)
from jarvis.tools.validator import (
    ToolCallLog,
    ToolCallValidator,
    safe_execute,
)


# ─── Pydantic Schemas (reuse validator patterns) ──────────────


class DeleteParams(BaseModel):
    file_paths: list[str] = Field(..., min_length=1)
    recursive: bool = False


class SearchParams(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


# ─── Execute Functions ────────────────────────────────────────


async def mock_delete(validated: DeleteParams) -> dict:
    return {"deleted": len(validated.file_paths), "paths": validated.file_paths}


async def mock_search(validated: SearchParams) -> dict:
    return {"query": validated.query, "results": [], "count": 0}


async def mock_fail(_params):
    raise ValueError("Simulated execution failure")


async def mock_slow(_params):
    await asyncio.sleep(1.0)  # fast enough for tests, slow enough to measure
    return {"done": True}


# ─── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def audit() -> AuditTrail:
    """Create an AuditTrail in a temp directory, auto-archive off."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="audit_test_")
    db = os.path.join(tmp, "audit.db")
    trail = AuditTrail(db_path=db, auto_archive=False)
    yield trail
    # Cleanup
    trail.vacuum()
    try:
        os.remove(db)
        os.rmdir(tmp)
    except OSError:
        pass


@pytest.fixture
def validator() -> ToolCallValidator:
    v = ToolCallValidator()
    v.register("delete", DeleteParams)
    v.register("search", SearchParams)
    return v


# ─── Test 1: record() writes a record ─────────────────────────


def test_record_writes_record(audit):
    record_id = audit.record(
        tool_name="delete",
        params={"file_paths": ["/tmp/test.txt"], "recursive": False},
        result={"deleted": 1},
        latency_ms=42.5,
        validation_passed=True,
        attempt=1,
        agent_name="file-agent",
        task_id="task-001",
        trace_id="trace-abc",
    )
    assert record_id > 0
    assert audit.count() == 1


# ─── Test 2: by_tool_name query ───────────────────────────────


def test_by_tool_name(audit):
    audit.record(tool_name="delete", params={"file_paths": ["/a"]})
    audit.record(tool_name="delete", params={"file_paths": ["/b"]})
    audit.record(tool_name="search", params={"query": "hello"})

    results = audit.by_tool_name("delete")
    assert len(results) == 2
    assert all(r.tool_name == "delete" for r in results)


# ─── Test 3: by_agent query ───────────────────────────────────


def test_by_agent(audit):
    audit.record(tool_name="delete", params={}, agent_name="file-agent")
    audit.record(tool_name="search", params={}, agent_name="search-agent")
    audit.record(tool_name="open", params={}, agent_name="file-agent")

    results = audit.by_agent("file-agent")
    assert len(results) == 2
    assert all(r.agent_name == "file-agent" for r in results)


# ─── Test 4: by_time_range query ──────────────────────────────


def test_by_time_range(audit):
    from datetime import datetime, timedelta

    audit.record(tool_name="delete", params={})

    now = datetime.utcnow()
    start = (now - timedelta(hours=1)).isoformat()
    end = (now + timedelta(hours=1)).isoformat()

    results = audit.by_time_range(start, end)
    assert len(results) == 1


# ─── Test 5: by_task query ────────────────────────────────────


def test_by_task(audit):
    audit.record(tool_name="delete", params={}, task_id="task-42")
    audit.record(tool_name="search", params={}, task_id="task-42")
    audit.record(tool_name="open", params={}, task_id="task-99")

    results = audit.by_task("task-42")
    assert len(results) == 2
    assert all(r.task_id == "task-42" for r in results)


# ─── Test 6: get_failed ───────────────────────────────────────


def test_get_failed(audit):
    audit.record(tool_name="delete", params={}, validation_passed=True, error=None)
    audit.record(tool_name="search", params={}, validation_passed=False, error="ValidationError")
    audit.record(tool_name="open", params={}, validation_passed=True, error="RuntimeError")

    failed = audit.get_failed()
    assert len(failed) == 2
    tool_names = {r.tool_name for r in failed}
    assert tool_names == {"search", "open"}


# ─── Test 7: get_slow ─────────────────────────────────────────


def test_get_slow(audit):
    audit.record(tool_name="fast", params={}, latency_ms=10.0)
    audit.record(tool_name="medium", params={}, latency_ms=200.0)
    audit.record(tool_name="slow", params={}, latency_ms=10000.0)

    slow = audit.get_slow(threshold_ms=5000.0)
    assert len(slow) == 1
    assert slow[0].tool_name == "slow"


# ─── Test 8: record_from_log ──────────────────────────────────


def test_record_from_log(audit):
    log = ToolCallLog(
        tool_name="delete",
        params={"file_paths": ["/x.txt"]},
        result={"deleted": 1},
        error=None,
        latency_ms=15.0,
        validation_passed=True,
        attempt=1,
    )

    record_id = audit.record_from_log(
        log,
        agent_name="tester",
        task_id="task-log",
        trace_id="trace-log",
    )
    assert record_id > 0

    results = audit.by_agent("tester")
    assert len(results) == 1
    assert results[0].tool_name == "delete"
    assert results[0].task_id == "task-log"


# ─── Test 9: AuditReplayer.replay_trace ───────────────────────


def test_replay_trace(audit):
    trace_id = "trace-replay-001"

    audit.record(
        tool_name="search", params={"query": "hello"},
        latency_ms=10.0, trace_id=trace_id, attempt=1,
    )
    audit.record(
        tool_name="delete", params={"file_paths": ["/a"]},
        latency_ms=25.0, trace_id=trace_id, attempt=1, error="Failed",
    )
    audit.record(
        tool_name="search", params={"query": "world"},
        latency_ms=15.0, trace_id=trace_id, attempt=2,
    )

    replayer = AuditReplayer(audit)
    table = replayer.replay_trace(trace_id)

    # Check that it's Markdown
    assert "|" in table
    assert "`search`" in table
    assert "`delete`" in table
    assert "FAIL" in table  # the delete with error
    assert "Summary:" in table
    assert "3 calls" in table


def test_replay_trace_empty(audit):
    replayer = AuditReplayer(audit)
    result = replayer.replay_trace("nonexistent")
    assert "No audit records found" in result


def test_replay_traces_multiple(audit):
    audit.record(tool_name="open", params={}, trace_id="t1")
    audit.record(tool_name="open", params={}, trace_id="t2")

    replayer = AuditReplayer(audit)
    result = replayer.replay_traces(["t1", "t2"])
    assert "## Trace `t1`" in result
    assert "## Trace `t2`" in result


# ─── Test 10: archive_old ─────────────────────────────────────


def test_archive_old(audit):
    # Insert a record with an old timestamp manually
    from datetime import datetime, timedelta
    old_ts = (datetime.utcnow() - timedelta(days=60)).isoformat()

    audit.record(tool_name="old_tool", params={"x": 1})
    # Manually update timestamp of the first record
    with audit._get_conn() as conn:
        conn.execute("UPDATE tool_audit SET timestamp = ? WHERE id = 1", (old_ts,))
        conn.commit()

    # Archive with a short age
    archived = audit.archive_old(age_days=30)
    assert archived == 1

    # Verify the record is gone
    assert audit.count() == 0

    # Verify archive file exists
    archive_dir = Path("jarvis_data/audit_archive")
    files = list(archive_dir.glob("audit_*.json.gz"))
    assert len(files) > 0

    # Cleanup archive files
    for f in files:
        f.unlink()
    if archive_dir.exists():
        archive_dir.rmdir()


# ─── Test 11: Concurrent writes ───────────────────────────────


def test_concurrent_writes(audit):
    n_threads = 10
    n_records_per_thread = 5

    def worker(thread_id):
        for i in range(n_records_per_thread):
            audit.record(
                tool_name=f"tool_{thread_id}",
                params={"iter": i},
                agent_name=f"agent_{thread_id}",
            )

    with ThreadPoolExecutor(max_workers=n_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(n_threads)]
        for f in futures:
            f.result()

    assert audit.count() == n_threads * n_records_per_thread


# ─── Test 12: Integration — safe_execute with audit_trail ─────


@pytest.mark.asyncio
async def test_safe_execute_with_audit_trail(audit, validator):
    result = await safe_execute(
        tool_name="delete",
        params={"file_paths": ["/test/path.txt"]},
        execute_fn=mock_delete,
        validator=validator,
        audit_trail=audit,
        audit_ctx={
            "agent_name": "file-agent",
            "task_id": "task-integration",
            "trace_id": "trace-integration",
        },
    )
    assert result.success is True
    assert result.result == {"deleted": 1, "paths": ["/test/path.txt"]}

    # Verify audit record was written
    records = audit.by_task("task-integration")
    assert len(records) == 1
    r = records[0]
    assert r.tool_name == "delete"
    assert r.agent_name == "file-agent"
    assert r.trace_id == "trace-integration"
    assert r.validation_passed is True


@pytest.mark.asyncio
async def test_safe_execute_without_audit_trail(validator):
    """safe_execute should work fine without audit_trail."""
    result = await safe_execute(
        tool_name="delete",
        params={"file_paths": ["/x"]},
        execute_fn=mock_delete,
        validator=validator,
    )
    assert result.success is True


# ─── Test 13: get_recent ──────────────────────────────────────


def test_get_recent(audit):
    for i in range(10):
        audit.record(tool_name=f"tool_{i}", params={})
    recent = audit.get_recent(limit=5)
    assert len(recent) == 5
    # Most recent first
    assert recent[0].id > recent[-1].id


# ─── Test 14: count ───────────────────────────────────────────


def test_count(audit):
    assert audit.count() == 0
    audit.record(tool_name="a", params={})
    audit.record(tool_name="b", params={})
    assert audit.count() == 2


# ─── Test 15: _safe_json_dumps edge cases ─────────────────────


def test_safe_json_dumps_serializable():
    result = _safe_json_dumps({"key": "value"})
    parsed = json.loads(result)
    assert parsed["key"] == "value"


def test_safe_json_dumps_large_truncation():
    large = "x" * 20000
    result = _safe_json_dumps({"data": large})
    assert len(result) <= 10000 + 10  # ~10 chars for structure + "..."


def test_safe_json_dumps_non_serializable():
    class NonSerializable:
        pass

    result = _safe_json_dumps({"obj": NonSerializable()})
    parsed = json.loads(result)
    assert "_serialization_error" in parsed["obj"] or "NonSerializable" in str(parsed)


# ─── Test 16: AuditRecord.from_row ────────────────────────────


def test_audit_record_from_row():
    row = (1, "2026-08-02T00:00:00", "agent", "task", "tool",
           '{"x":1}', '{"y":2}', None, 100.5, 1, 1, "trace-1")
    record = AuditRecord.from_row(row)
    assert record.id == 1
    assert record.tool_name == "tool"
    assert record.validation_passed is True
    assert record.latency_ms == 100.5


def test_audit_record_to_dict():
    row = (1, "2026-08-02T00:00:00", "agent", "task", "tool",
           '{"x":1}', '{"y":2}', None, 100.5, 1, 1, "trace-1")
    record = AuditRecord.from_row(row)
    d = record.to_dict()
    assert d["id"] == 1
    assert d["tool_name"] == "tool"
