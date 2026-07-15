"""SQLite persistence layer for Emperor Dashboard.

Provides a lightweight Database class backed by sqlite3 (stdlib only).
Task history, evolution history, and alert history are persisted across
restarts so the Dashboard never loses data.
"""

from __future__ import annotations

import logging
import sqlite3
from threading import RLock
from typing import Any, Optional

logger = logging.getLogger("jarvis.database")

DDL_TASK_HISTORY = """\
CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    minister TEXT,
    result TEXT,
    confidence REAL,
    status TEXT DEFAULT 'completed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_EVOLUTION_HISTORY = """\
CREATE TABLE IF NOT EXISTS evolution_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation INTEGER NOT NULL,
    minister_name TEXT NOT NULL,
    merit_before REAL,
    merit_after REAL,
    delta REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_ALERT_HISTORY = """\
CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    """SQLite-backed persistence for Emperor system data.

    Usage:
        db = Database("path/to/jarvis.db")
        db.save_task("abc", "What is 2+2?", "turing", "4", 0.95, "completed")
        rows = db.get_task_history(limit=50)
    """

    def __init__(self, db_path: str) -> None:
        """Open (or create) the SQLite database and ensure tables exist.

        Args:
            db_path: Absolute path to the .db file.
        """
        self._db_path = db_path
        self._lock = RLock()

        self._conn: sqlite3.Connection = sqlite3.connect(
            db_path,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._ensure_tables()
        logger.info("[Database] Initialized at %s", db_path)

    # ── Table bootstrap ────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        """Create tables if they don't exist."""
        with self._lock:
            self._conn.execute(DDL_TASK_HISTORY)
            self._conn.execute(DDL_EVOLUTION_HISTORY)
            self._conn.execute(DDL_ALERT_HISTORY)
            self._conn.commit()

    # ── Task history ───────────────────────────────────────────────

    def save_task(
        self,
        task_id: str,
        prompt: str,
        minister: Optional[str],
        result: Optional[str],
        confidence: Optional[float],
        status: str = "completed",
    ) -> int:
        """Insert a task record and return the row id."""
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO task_history (task_id, prompt, minister, result, confidence, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (task_id, prompt, minister, result, confidence, status),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_task_history(self, limit: int = 50) -> list[dict]:
        """Return the most recent N task records (newest first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM task_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Evolution history ──────────────────────────────────────────

    def save_evolution(
        self,
        generation: int,
        minister_name: str,
        merit_before: Optional[float],
        merit_after: Optional[float],
        delta: Optional[float],
    ) -> int:
        """Insert an evolution record and return the row id."""
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO evolution_history (generation, minister_name, merit_before, merit_after, delta)
                   VALUES (?, ?, ?, ?, ?)""",
                (generation, minister_name, merit_before, merit_after, delta),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_evolution_history(self, limit: int = 100) -> list[dict]:
        """Return the most recent N evolution records (newest first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM evolution_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Alert history ──────────────────────────────────────────────

    def save_alert(self, rule_name: str, level: str, message: str) -> int:
        """Insert an alert record and return the row id."""
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO alert_history (rule_name, level, message)
                   VALUES (?, ?, ?)""",
                (rule_name, level, message),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_alert_history(self, limit: int = 50) -> list[dict]:
        """Return the most recent N alert records (newest first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alert_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Utility ────────────────────────────────────────────────────

    def clear_all(self) -> None:
        """Truncate all three tables (for testing / reset)."""
        with self._lock:
            self._conn.execute("DELETE FROM task_history")
            self._conn.execute("DELETE FROM evolution_history")
            self._conn.execute("DELETE FROM alert_history")
            self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
            logger.info("[Database] Connection closed")
        except Exception:
            pass
