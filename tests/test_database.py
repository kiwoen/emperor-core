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
