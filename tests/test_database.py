"""Tests for jarvis.database — SQLite persistence layer.

Uses tempfile for isolated test databases that are cleaned up after each test.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from jarvis.database import Database


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def db():
    """Create a temporary database and yield it, then clean up."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(path)
    yield database
    database.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════
# Tests — General
# ══════════════════════════════════════════════════════════════════


def test_database_init_creates_file(db):
    """Database.__init__ should create the .db file and initialize tables."""
    assert os.path.exists(db._db_path)
    assert os.path.getsize(db._db_path) > 0


def test_database_tables_exist(db):
    """All three tables should exist after init."""
    rows = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "task_history" in names
    assert "evolution_history" in names
    assert "alert_history" in names


# ══════════════════════════════════════════════════════════════════
# Tests — task_history CRUD
# ══════════════════════════════════════════════════════════════════


def test_save_task_returns_rowid(db):
    """save_task should return an integer row id."""
    rid = db.save_task("t1", "Hello", "turing", "Hi", 0.9, "completed")
    assert isinstance(rid, int)
    assert rid > 0


def test_save_and_get_task_history(db):
    """Insert a task and verify it can be retrieved."""
    db.save_task("abc123", "What is 2+2?", "turing", "4", 0.95, "completed")
    rows = db.get_task_history(limit=10)
    assert len(rows) == 1
    r = rows[0]
    assert r["task_id"] == "abc123"
    assert r["prompt"] == "What is 2+2?"
    assert r["minister"] == "turing"
    assert r["result"] == "4"
    assert r["confidence"] == pytest.approx(0.95)
    assert r["status"] == "completed"


def test_task_history_limit(db):
    """get_task_history should respect the limit parameter."""
    for i in range(10):
        db.save_task(f"t{i}", f"prompt {i}", "min", "ok", 0.8, "completed")
    rows = db.get_task_history(limit=3)
    assert len(rows) == 3


def test_task_history_newest_first(db):
    """get_task_history should return newest rows first."""
    db.save_task("t1", "first", "a", "r1", 0.5, "completed")
    db.save_task("t2", "second", "b", "r2", 0.6, "completed")
    rows = db.get_task_history(limit=10)
    assert rows[0]["task_id"] == "t2"
    assert rows[1]["task_id"] == "t1"


def test_task_history_nullable_fields(db):
    """minister, result, and confidence can be None."""
    db.save_task("nx", "no minister", None, None, None, "failed")
    rows = db.get_task_history()
    assert rows[0]["minister"] is None
    assert rows[0]["result"] is None
    assert rows[0]["confidence"] is None
    assert rows[0]["status"] == "failed"


# ══════════════════════════════════════════════════════════════════
# Tests — evolution_history CRUD
# ══════════════════════════════════════════════════════════════════


def test_save_evolution_returns_rowid(db):
    """save_evolution should return an integer row id."""
    rid = db.save_evolution(1, "turing", 0.5, 0.8, 0.3)
    assert isinstance(rid, int)
    assert rid > 0


def test_save_and_get_evolution_history(db):
    """Insert evolution records and verify retrieval."""
    db.save_evolution(1, "turing", 0.5, 0.8, 0.3)
    db.save_evolution(2, "curie", 0.6, 0.9, 0.3)
    rows = db.get_evolution_history(limit=10)
    assert len(rows) == 2
    assert rows[0]["minister_name"] == "curie"  # newest first
    assert rows[1]["minister_name"] == "turing"


def test_evolution_history_limit(db):
    """get_evolution_history should respect limit."""
    for i in range(8):
        db.save_evolution(i, f"m{i}", 0.1, 0.2, 0.1)
    rows = db.get_evolution_history(limit=4)
    assert len(rows) == 4


def test_evolution_nullable_merit_fields(db):
    """merit_before / merit_after / delta can be None."""
    db.save_evolution(1, "test", None, None, None)
    rows = db.get_evolution_history()
    assert rows[0]["merit_before"] is None
    assert rows[0]["merit_after"] is None
    assert rows[0]["delta"] is None


# ══════════════════════════════════════════════════════════════════
# Tests — alert_history CRUD
# ══════════════════════════════════════════════════════════════════


def test_save_alert_returns_rowid(db):
    """save_alert should return an integer row id."""
    rid = db.save_alert("low_memory", "warning", "Memory below 10%")
    assert isinstance(rid, int)
    assert rid > 0


def test_save_and_get_alert_history(db):
    """Insert alert records and verify retrieval."""
    db.save_alert("low_mem", "warning", "Memory low")
    db.save_alert("cpu_spike", "critical", "CPU at 99%")
    rows = db.get_alert_history(limit=10)
    assert len(rows) == 2
    assert rows[0]["rule_name"] == "cpu_spike"  # newest first
    assert rows[1]["rule_name"] == "low_mem"
    assert rows[0]["level"] == "critical"
    assert rows[1]["level"] == "warning"


def test_alert_history_limit(db):
    """get_alert_history should respect limit."""
    for i in range(10):
        db.save_alert(f"rule_{i}", "info", f"message {i}")
    rows = db.get_alert_history(limit=5)
    assert len(rows) == 5


# ══════════════════════════════════════════════════════════════════
# Tests — clear_all
# ══════════════════════════════════════════════════════════════════


def test_clear_all_removes_all_rows(db):
    """clear_all should empty all three tables."""
    db.save_task("t", "p", "m", "r", 0.5, "ok")
    db.save_evolution(1, "m", 0.1, 0.2, 0.1)
    db.save_alert("r", "warn", "msg")

    db.clear_all()

    assert len(db.get_task_history()) == 0
    assert len(db.get_evolution_history()) == 0
    assert len(db.get_alert_history()) == 0


# ══════════════════════════════════════════════════════════════════
# Test — empty history returns []
# ══════════════════════════════════════════════════════════════════


def test_empty_history_returns_empty_list(db):
    """Fresh database returns empty lists for all history queries."""
    assert db.get_task_history() == []
    assert db.get_evolution_history() == []
    assert db.get_alert_history() == []


# ══════════════════════════════════════════════════════════════════
# Tests — task_history filtering
# ══════════════════════════════════════════════════════════════════


def test_task_history_filter_by_minister(db):
    """get_task_history should filter by minister name."""
    db.save_task("t1", "prompt1", "turing", "r1", 0.9, "completed")
    db.save_task("t2", "prompt2", "curie", "r2", 0.8, "completed")
    db.save_task("t3", "prompt3", "turing", "r3", 0.7, "failed")

    rows = db.get_task_history(minister="turing")
    assert len(rows) == 2
    assert all(r["minister"] == "turing" for r in rows)


def test_task_history_filter_by_status(db):
    """get_task_history should filter by status."""
    db.save_task("t1", "p1", "m1", "r1", 0.9, "completed")
    db.save_task("t2", "p2", "m2", "r2", 0.8, "failed")
    db.save_task("t3", "p3", "m3", "r3", 0.7, "completed")

    rows = db.get_task_history(status="failed")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"


def test_task_history_filter_by_search(db):
    """get_task_history should fuzzy-search prompt content."""
    db.save_task("t1", "solve math equation", "m1", "42", 0.9, "completed")
    db.save_task("t2", "write python code", "m2", "ok", 0.8, "completed")
    db.save_task("t3", "analyze data", "m3", "ok", 0.7, "completed")

    rows = db.get_task_history(search="math")
    assert len(rows) == 1
    assert "math" in rows[0]["prompt"]

    rows = db.get_task_history(search="python")
    assert len(rows) == 1
    assert "python" in rows[0]["prompt"]

    rows = db.get_task_history(search="xyzzy")
    assert len(rows) == 0


def test_task_history_offset(db):
    """get_task_history should support pagination offset."""
    for i in range(5):
        db.save_task(f"t{i}", f"prompt{i}", "m", "ok", 0.5, "completed")

    all_rows = db.get_task_history(limit=10)
    assert len(all_rows) == 5

    page1 = db.get_task_history(limit=2, offset=0)
    page2 = db.get_task_history(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    # Verify no overlap
    ids_page1 = {r["id"] for r in page1}
    ids_page2 = {r["id"] for r in page2}
    assert ids_page1.isdisjoint(ids_page2)


def test_task_history_combined_filters(db):
    """get_task_history with multiple filters combined (AND logic)."""
    db.save_task("t1", "hello world", "turing", "ok", 0.9, "completed")
    db.save_task("t2", "hello again", "turing", "ok", 0.8, "failed")
    db.save_task("t3", "hello world", "curie", "ok", 0.7, "completed")

    rows = db.get_task_history(minister="turing", status="completed", search="hello")
    assert len(rows) == 1
    assert rows[0]["task_id"] == "t1"


# ══════════════════════════════════════════════════════════════════
# Tests — alert_history filtering
# ══════════════════════════════════════════════════════════════════


def test_alert_history_filter_by_level(db):
    """get_alert_history should filter by level."""
    db.save_alert("rule1", "WARNING", "msg1")
    db.save_alert("rule2", "ERROR", "msg2")
    db.save_alert("rule3", "WARNING", "msg3")

    rows = db.get_alert_history(level="WARNING")
    assert len(rows) == 2
    assert all(r["level"] == "WARNING" for r in rows)

    rows = db.get_alert_history(level="ERROR")
    assert len(rows) == 1
    assert rows[0]["level"] == "ERROR"


def test_alert_history_filter_by_search(db):
    """get_alert_history should fuzzy-search message content."""
    db.save_alert("r1", "WARNING", "Memory usage high")
    db.save_alert("r2", "ERROR", "CPU spike detected")
    db.save_alert("r3", "INFO", "Disk space low")

    rows = db.get_alert_history(search="memory")
    assert len(rows) == 1
    assert "Memory" in rows[0]["message"]

    rows = db.get_alert_history(search="cpu")
    assert len(rows) == 1

    rows = db.get_alert_history(search="nope")
    assert len(rows) == 0


def test_alert_history_offset(db):
    """get_alert_history should support pagination offset."""
    for i in range(5):
        db.save_alert(f"r{i}", "INFO", f"msg{i}")

    page1 = db.get_alert_history(limit=2, offset=0)
    page2 = db.get_alert_history(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    ids1 = {r["id"] for r in page1}
    ids2 = {r["id"] for r in page2}
    assert ids1.isdisjoint(ids2)


# ══════════════════════════════════════════════════════════════════
# Tests — export_all
# ══════════════════════════════════════════════════════════════════


def test_export_all_empty(db):
    """export_all should return empty lists when no data."""
    result = db.export_all()
    assert result == {"tasks": [], "evolutions": [], "alerts": []}


def test_export_all_with_data(db):
    """export_all should return all data from all three tables."""
    db.save_task("t1", "prompt1", "m1", "r1", 0.9, "completed")
    db.save_task("t2", "prompt2", "m2", "r2", 0.8, "failed")
    db.save_evolution(1, "m1", 0.5, 0.8, 0.3)
    db.save_alert("rule1", "WARNING", "test alert")

    result = db.export_all()
    assert len(result["tasks"]) == 2
    assert len(result["evolutions"]) == 1
    assert len(result["alerts"]) == 1
    # Verify newest first ordering
    assert result["tasks"][0]["task_id"] == "t2"
    assert result["tasks"][1]["task_id"] == "t1"
