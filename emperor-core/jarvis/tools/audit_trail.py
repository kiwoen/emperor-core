"""
Tool Call Audit Trail — Persistent audit logging for tool calls.

Provides persistent (SQLite) audit logging of every tool call executed
via safe_execute, with query APIs, trace replay, and auto-archival.

Usage::

    from jarvis.tools.audit_trail import AuditTrail, AuditReplayer

    audit = AuditTrail()
    audit.record(log)  # called automatically from safe_execute

    # Query
    failed = audit.get_failed()
    slow = audit.get_slow(threshold_ms=5000)

    # Replay a trace
    replayer = AuditReplayer(audit)
    table = replayer.replay_trace(trace_id="abc123")

Database lives at jarvis_data/audit.db (auto-created).
Logs older than 30 days are auto-archived to audit_archive/.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.tools.audit_trail")

# ── Constants ─────────────────────────────────────────────────
_DEFAULT_DB_PATH = "jarvis_data/audit.db"
_ARCHIVE_AGE_DAYS = 30
_ARCHIVE_DIR = "jarvis_data/audit_archive"

# ── SQL Schema ────────────────────────────────────────────────
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tool_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    agent_name  TEXT    NOT NULL DEFAULT '',
    task_id     TEXT    NOT NULL DEFAULT '',
    tool_name   TEXT    NOT NULL,
    params      TEXT    NOT NULL DEFAULT '{}',
    result      TEXT    DEFAULT NULL,
    error       TEXT    DEFAULT NULL,
    latency_ms  REAL    NOT NULL DEFAULT 0.0,
    validation_passed INTEGER NOT NULL DEFAULT 1,
    attempt     INTEGER NOT NULL DEFAULT 1,
    trace_id    TEXT    NOT NULL DEFAULT ''
);
"""

_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_audit_tool_name ON tool_audit(tool_name);",
    "CREATE INDEX IF NOT EXISTS idx_audit_agent_name ON tool_audit(agent_name);",
    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON tool_audit(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_audit_task_id ON tool_audit(task_id);",
    "CREATE INDEX IF NOT EXISTS idx_audit_trace_id ON tool_audit(trace_id);",
    "CREATE INDEX IF NOT EXISTS idx_audit_validation ON tool_audit(validation_passed);",
]


# ═══════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════

@dataclass
class AuditRecord:
    """A single audit record matching the SQL schema."""

    id: int
    timestamp: str
    agent_name: str
    task_id: str
    tool_name: str
    params: str
    result: Optional[str]
    error: Optional[str]
    latency_ms: float
    validation_passed: bool
    attempt: int
    trace_id: str

    @classmethod
    def from_row(cls, row: tuple) -> AuditRecord:
        return cls(
            id=row[0],
            timestamp=row[1],
            agent_name=row[2],
            task_id=row[3],
            tool_name=row[4],
            params=row[5],
            result=row[6],
            error=row[7],
            latency_ms=row[8],
            validation_passed=bool(row[9]),
            attempt=row[10],
            trace_id=row[11],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "agent_name": self.agent_name,
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "validation_passed": self.validation_passed,
            "attempt": self.attempt,
            "trace_id": self.trace_id,
        }


# ═══════════════════════════════════════════════════════════════
# AuditTrail
# ═══════════════════════════════════════════════════════════════

class AuditTrail:
    """Persistent audit trail backed by SQLite.

    Auto-creates the DB and tables on first use. Thread-safe via
    check_same_thread=False and a local lock.
    """

    def __init__(self, db_path: Optional[str] = None, auto_archive: bool = True):
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.auto_archive = auto_archive
        self._lock = threading.Lock()

        # Ensure parent directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Internal ───────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            for idx_sql in _CREATE_INDEXES_SQL:
                conn.execute(idx_sql)
            conn.commit()
        logger.info("Audit trail database initialised at %s", self.db_path)

    # ── Record ─────────────────────────────────────────────

    def record(
        self,
        tool_name: str,
        params: dict = None,
        result: Any = None,
        error: Optional[str] = None,
        latency_ms: float = 0.0,
        validation_passed: bool = True,
        attempt: int = 1,
        agent_name: str = "",
        task_id: str = "",
        trace_id: str = "",
    ) -> int:
        """Record a tool call to the audit trail. Returns the new row id."""
        params_str = _safe_json_dumps(params or {})
        result_str = _safe_json_dumps(result) if result is not None else None
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    """INSERT INTO tool_audit
                       (timestamp, agent_name, task_id, tool_name, params, result,
                        error, latency_ms, validation_passed, attempt, trace_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        timestamp,
                        agent_name,
                        task_id,
                        tool_name,
                        params_str,
                        result_str,
                        error,
                        latency_ms,
                        1 if validation_passed else 0,
                        attempt,
                        trace_id,
                    ),
                )
                conn.commit()
                row_id = cursor.lastrowid

        # Trigger auto-archive check (best-effort, fire-and-forget)
        if self.auto_archive:
            self._maybe_archive()

        return row_id

    def record_from_log(
        self,
        log,  # ToolCallLog from validator
        agent_name: str = "",
        task_id: str = "",
        trace_id: str = "",
    ) -> int:
        """Convenience: record from a ToolCallLog instance."""
        return self.record(
            tool_name=log.tool_name,
            params=log.params,
            result=log.result,
            error=log.error,
            latency_ms=log.latency_ms,
            validation_passed=log.validation_passed,
            attempt=log.attempt,
            agent_name=agent_name,
            task_id=task_id,
            trace_id=trace_id,
        )

    # ── Query APIs ─────────────────────────────────────────

    def by_tool_name(self, tool_name: str, limit: int = 100) -> list[AuditRecord]:
        """Return audit records for a specific tool, most recent first."""
        return self._query(
            "SELECT * FROM tool_audit WHERE tool_name = ? ORDER BY id DESC LIMIT ?",
            (tool_name, limit),
        )

    def by_agent(self, agent_name: str, limit: int = 100) -> list[AuditRecord]:
        """Return audit records for a specific agent, most recent first."""
        return self._query(
            "SELECT * FROM tool_audit WHERE agent_name = ? ORDER BY id DESC LIMIT ?",
            (agent_name, limit),
        )

    def by_time_range(
        self,
        start: str,  # ISO format e.g. "2026-07-01T00:00:00"
        end: str,    # ISO format e.g. "2026-08-01T00:00:00"
        limit: int = 500,
    ) -> list[AuditRecord]:
        """Return audit records within a time range, most recent first."""
        return self._query(
            "SELECT * FROM tool_audit WHERE timestamp >= ? AND timestamp <= ? ORDER BY id DESC LIMIT ?",
            (start, end, limit),
        )

    def by_task(self, task_id: str, limit: int = 200) -> list[AuditRecord]:
        """Return audit records for a specific task."""
        return self._query(
            "SELECT * FROM tool_audit WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        )

    def get_failed(self, limit: int = 100) -> list[AuditRecord]:
        """Return records where validation or execution failed."""
        return self._query(
            "SELECT * FROM tool_audit WHERE validation_passed = 0 OR error IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    def get_slow(self, threshold_ms: float = 5000.0, limit: int = 100) -> list[AuditRecord]:
        """Return records where latency exceeds threshold."""
        return self._query(
            "SELECT * FROM tool_audit WHERE latency_ms >= ? ORDER BY latency_ms DESC LIMIT ?",
            (threshold_ms, limit),
        )

    def get_recent(self, limit: int = 50) -> list[AuditRecord]:
        """Return most recent audit records."""
        return self._query(
            "SELECT * FROM tool_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    def count(self) -> int:
        """Return total number of audit records."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM tool_audit").fetchone()
            return row[0] if row else 0

    # ── Internal query helper ──────────────────────────────

    def _query(self, sql: str, params: tuple = ()) -> list[AuditRecord]:
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [AuditRecord.from_row(tuple(r)) for r in rows]

    # ── Archive ────────────────────────────────────────────

    def _maybe_archive(self) -> None:
        """Check if archive is needed and run it.

        Checks on a ~1-hour basis to avoid doing this on every record().
        """
        # Simple rate-limit: use a file timestamp
        marker = Path(self.db_path).parent / ".last_archive_check"
        now = time.time()
        if marker.exists():
            try:
                last_check = float(marker.read_text().strip())
                if now - last_check < 3600.0:  # 1 hour
                    return
            except (ValueError, OSError):
                pass

        try:
            marker.write_text(str(now))
        except OSError:
            pass

        self.archive_old()

    def archive_old(self, age_days: int = _ARCHIVE_AGE_DAYS) -> int:
        """Archive records older than `age_days` to gzipped JSON files.

        Returns the number of records archived.
        """
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=age_days)).isoformat()

        with self._lock:
            with self._get_conn() as conn:
                # Select old records
                rows = conn.execute(
                    "SELECT * FROM tool_audit WHERE timestamp < ? ORDER BY id",
                    (cutoff,),
                ).fetchall()

                if not rows:
                    return 0

                records = [AuditRecord.from_row(tuple(r)) for r in rows]
                ids = [r.id for r in records]

                # Write archive file
                archive_dir = Path(_ARCHIVE_DIR)
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_name = (
                    f"audit_{records[0].timestamp[:10]}_{records[-1].timestamp[:10]}.json.gz"
                )
                archive_path = archive_dir / archive_name

                payload = json.dumps(
                    [r.to_dict() for r in records],
                    ensure_ascii=False,
                    default=str,
                )
                with gzip.open(archive_path, "wt", encoding="utf-8") as f:
                    f.write(payload)

                # Delete archived records
                conn.execute(
                    f"DELETE FROM tool_audit WHERE id IN ({','.join(['?'] * len(ids))})",
                    ids,
                )
                conn.commit()

        logger.info(
            "Archived %d audit records to %s", len(records), archive_path,
        )
        return len(records)

    # ── Housekeeping ───────────────────────────────────────

    def vacuum(self) -> None:
        """Compact the SQLite database."""
        with self._get_conn() as conn:
            conn.execute("VACUUM")

    def close(self) -> None:
        """No-op; connections are ephemeral per-operation."""
        pass


# ═══════════════════════════════════════════════════════════════
# AuditReplayer
# ═══════════════════════════════════════════════════════════════

class AuditReplayer:
    """Replay a complete tool call chain for a given trace_id.

    Outputs a Markdown table showing the ordered sequence of
    tool calls within a trace.
    """

    def __init__(self, audit: AuditTrail):
        self.audit = audit

    def replay_trace(self, trace_id: str) -> str:
        """Return a Markdown table of all tool calls in the given trace.

        Records are sorted by timestamp ascending (order of execution).
        """
        rows = self._get_trace_rows(trace_id)
        if not rows:
            return f"_No audit records found for trace `{trace_id}`._"

        return self._build_markdown_table(rows)

    def replay_traces(self, trace_ids: list[str]) -> str:
        """Return a combined Markdown report for multiple traces."""
        parts = []
        for tid in trace_ids:
            parts.append(f"## Trace `{tid}`\n")
            parts.append(self.replay_trace(tid))
            parts.append("\n")
        return "\n".join(parts)

    def _get_trace_rows(self, trace_id: str) -> list[AuditRecord]:
        with self.audit._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_audit WHERE trace_id = ? ORDER BY timestamp ASC, id ASC",
                (trace_id,),
            ).fetchall()
        return [AuditRecord.from_row(tuple(r)) for r in rows]

    def _build_markdown_table(self, records: list[AuditRecord]) -> str:
        header = (
            "| # | Timestamp | Tool | Latency (ms) | Valid | Attempt | Status |\n"
            "|---|-----------|------|-------------|-------|---------|--------|"
        )
        lines = [header]
        for i, r in enumerate(records, 1):
            status = "OK" if (r.validation_passed and not r.error) else "FAIL"
            ts_short = r.timestamp[:19] if len(r.timestamp) > 19 else r.timestamp
            lines.append(
                f"| {i} | {ts_short} | `{r.tool_name}` | "
                f"{r.latency_ms:.1f} | "
                f"{'PASS' if r.validation_passed else 'FAIL'} | "
                f"{r.attempt} | {status} |"
            )

        # Append summary
        total_latency = sum(r.latency_ms for r in records)
        ok_count = sum(1 for r in records if r.validation_passed and not r.error)
        fail_count = len(records) - ok_count
        summary = (
            f"\n**Summary:** {len(records)} calls, "
            f"{ok_count} OK, {fail_count} failed, "
            f"total latency {total_latency:.1f} ms\n"
        )
        return "\n".join(lines) + summary


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _safe_json_dumps(obj: Any) -> str:
    """JSON-serialise an object, with truncation for large values."""
    try:
        result = json.dumps(obj, ensure_ascii=False, default=_json_default)
        if len(result) > 10000:
            return result[:10000] + "..."
        return result
    except (TypeError, ValueError) as e:
        return json.dumps(
            {"_serialization_error": str(e), "_type": str(type(obj))},
            ensure_ascii=False,
        )


def _json_default(obj: Any) -> Any:
    """Default handler for JSON serialisation of non-standard types."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)[:1000]
