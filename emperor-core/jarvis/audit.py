"""
Execution Audit Trail — immutable, replayable execution logging.

2026 年最佳实践：每一次有意义的操作都必须留下不可篡改的审计记录。
审计是 "什么变了？谁有过权限？怎么回滚？" 的唯一可靠答案。

Architecture:
    AuditEntry  — 单条审计记录
    AuditLogger — 追加写入器（SQLite 存储）
    AuditReader — 只读查询器

Database schema:
    audit_trail (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id     TEXT NOT NULL,
        step         INTEGER NOT NULL,
        phase        TEXT NOT NULL,       -- "before" | "capability" | "after" | "pipeline"
        actor        TEXT,                -- minister name or "emperor"
        action       TEXT NOT NULL,       -- "task.execute" | "capability.invoke" | "pipeline.run"
        input_summary TEXT,               -- 压缩后的输入摘要（前 200 字符）
        output_summary TEXT,              -- 压缩后的输出摘要（前 500 字符）
        extra_json  TEXT,                 -- 额外结构化数据
        success     INTEGER DEFAULT 0,
        error_msg   TEXT,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )

Usage:
    from jarvis.audit import AuditLogger

    logger = AuditLogger("path/to/audit.db")
    logger.log(
        trace_id="task_abc123",
        step=1,
        phase="capability",
        action="capability.invoke",
        input_summary="查询北京天气",
        output_summary="北京: 25°C, 晴",
        success=True,
    )
    reader = logger.reader()
    events = reader.query_by_trace("task_abc123")
"""

from __future__ import annotations

import json as _json
import logging
import sqlite3
import uuid
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jarvis.audit")


# ── SQL Schema ──

DDL_AUDIT_TRAIL = """\
CREATE TABLE IF NOT EXISTS audit_trail (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT NOT NULL,
    step         INTEGER NOT NULL DEFAULT 0,
    phase        TEXT NOT NULL,
    actor        TEXT DEFAULT '',
    action       TEXT NOT NULL,
    input_summary  TEXT DEFAULT '',
    output_summary TEXT DEFAULT '',
    extra_json   TEXT DEFAULT '{}',
    success      INTEGER DEFAULT 0,
    error_msg    TEXT DEFAULT '',
    duration_ms  REAL DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_AUDIT_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_trail(trace_id);",
    "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_trail(action);",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_trail(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_audit_phase ON audit_trail(phase);",
]


# ══════════════════════════════════════════════════════════════════
# Data Types
# ══════════════════════════════════════════════════════════════════


@dataclass
class AuditEntry:
    """一条不可变的审计记录。"""

    trace_id: str
    step: int
    phase: str
    action: str
    actor: str = ""
    input_summary: str = ""
    output_summary: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error_msg: str = ""
    duration_ms: float = 0
    created_at: str = ""

    def to_row(self) -> tuple:
        """转换为 SQLite 行元组（不含 id 和 created_at）。"""
        return (
            self.trace_id,
            self.step,
            self.phase,
            self.actor or "",
            self.action,
            (self.input_summary or "")[:200],
            (self.output_summary or "")[:500],
            _json.dumps(self.extra, ensure_ascii=False),
            1 if self.success else 0,
            (self.error_msg or "")[:500],
            self.duration_ms,
        )

    @classmethod
    def from_row(cls, row: tuple) -> AuditEntry:
        """从 SQLite 行元组重建。"""
        # row: (id, trace_id, step, phase, actor, action, input_summary,
        #       output_summary, extra_json, success, error_msg, duration_ms, created_at)
        return cls(
            trace_id=row[1],
            step=row[2],
            phase=row[3],
            action=row[5],
            actor=row[4],
            input_summary=row[6],
            output_summary=row[7],
            extra=_json.loads(row[8]) if row[8] else {},
            success=bool(row[9]),
            error_msg=row[10] or "",
            duration_ms=row[11] or 0,
            created_at=row[12] or "",
        )


# ══════════════════════════════════════════════════════════════════
# AuditLogger — 追加写入
# ══════════════════════════════════════════════════════════════════


class AuditLogger:
    """不可篡改的审计日志写入器。

    写入策略：
    - SQLite WAL 模式，保证并发写入安全
    - 每一条都立即 fsync（不批量），防止意外丢失
    - 仅追加，不提供修改/删除 API
    """

    def __init__(self, db_path: str):
        self._db_path = str(Path(db_path).resolve())
        self._lock = RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """初始化数据库表结构（幂等）。"""
        with self._get_conn() as conn:
            conn.execute(DDL_AUDIT_TRAIL)
            for idx_sql in DDL_AUDIT_INDEXES:
                conn.execute(idx_sql)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """获取连接（惰性创建，WAL 模式）。"""
        if self._conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=FULL;")
            self._conn = conn
        return self._conn

    def log(self, **kwargs: Any) -> int:
        """追加一条审计记录。

        Args:
            trace_id: 跟踪 ID（同一个任务/流水线共享相同的 trace_id）
            step: 步骤编号
            phase: 阶段 ("before" | "capability" | "after" | "pipeline" | "evolve")
            action: 动作名称 ("task.execute" | "capability.invoke" | "pipeline.run")
            actor: 执行者
            input_summary: 输入摘要（≤200 字符）
            output_summary: 输出摘要（≤500 字符）
            extra: 额外结构化数据（dict）
            success: 是否成功
            error_msg: 错误信息
            duration_ms: 耗时

        Returns:
            写入的 row ID。
        """
        entry = AuditEntry(
            trace_id=kwargs.get("trace_id", str(uuid.uuid4())[:12]),
            step=kwargs.get("step", 0),
            phase=kwargs.get("phase", "unknown"),
            action=kwargs.get("action", "unknown"),
            actor=kwargs.get("actor", ""),
            input_summary=(kwargs.get("input_summary") or "")[:200],
            output_summary=(kwargs.get("output_summary") or "")[:500],
            extra=kwargs.get("extra", {}),
            success=kwargs.get("success", True),
            error_msg=kwargs.get("error_msg", ""),
            duration_ms=kwargs.get("duration_ms", 0),
        )

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO audit_trail
                   (trace_id, step, phase, actor, action, input_summary,
                    output_summary, extra_json, success, error_msg, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entry.to_row(),
            )
            conn.commit()
            last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        return last_id

    def log_task_before(self, trace_id: str, prompt: str, domain: str, minister: str = "") -> int:
        """记录任务发起（Phase: before）。"""
        return self.log(
            trace_id=trace_id,
            step=0,
            phase="before",
            action="task.execute",
            actor=minister or "emperor",
            input_summary=prompt[:200],
            extra={"domain": domain},
        )

    def log_capability_invoke(self, trace_id: str, step: int, cap_name: str,
                               prompt: str, result: str, success: bool,
                               duration_ms: float = 0, error: str = "") -> int:
        """记录能力调用（Phase: capability）。"""
        return self.log(
            trace_id=trace_id,
            step=step,
            phase="capability",
            action=f"capability.{cap_name}",
            input_summary=prompt[:200],
            output_summary=result[:500],
            success=success,
            duration_ms=duration_ms,
            error_msg=error,
        )

    def log_task_after(self, trace_id: str, step: int, success: bool,
                        result: str = "", duration_ms: float = 0, error: str = "") -> int:
        """记录任务完成（Phase: after）。"""
        return self.log(
            trace_id=trace_id,
            step=step,
            phase="after",
            action="task.complete",
            output_summary=result[:500],
            success=success,
            duration_ms=duration_ms,
            error_msg=error,
        )

    def log_pipeline_stage(self, trace_id: str, step: int, pipeline_name: str,
                            stage_name: str, success: bool,
                            duration_ms: float = 0, error: str = "") -> int:
        """记录流水线阶段（Phase: pipeline）。"""
        return self.log(
            trace_id=trace_id,
            step=step,
            phase="pipeline",
            action=f"pipeline.{pipeline_name}.{stage_name}",
            success=success,
            duration_ms=duration_ms,
            error_msg=error,
        )

    def log_evolve(self, trace_id: str, cycles: int, result: Dict[str, Any]) -> int:
        """记录进化事件（Phase: evolve）。"""
        return self.log(
            trace_id=trace_id,
            step=0,
            phase="evolve",
            action="court.evolve",
            input_summary=f"cycles={cycles}",
            extra=result,
        )

    def count(self) -> int:
        """返回总记录数。"""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()
            return row[0] if row else 0

    def size_bytes(self) -> int:
        """返回数据库文件大小（字节）。"""
        p = Path(self._db_path)
        return p.stat().st_size if p.is_file() else 0

    def reader(self) -> AuditReader:
        """返回只读查询器。"""
        return AuditReader(self._get_conn(), self._lock)

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


# ══════════════════════════════════════════════════════════════════
# AuditReader — 只读查询
# ══════════════════════════════════════════════════════════════════


class AuditReader:
    """只读审计查询器。

    所有查询方法返回 AuditEntry 列表，不会修改数据。
    """

    def __init__(self, conn: sqlite3.Connection, lock: RLock):
        self._conn = conn
        self._lock = lock

    def query_by_trace(self, trace_id: str) -> List[AuditEntry]:
        """查询某个 trace 的完整步骤链路。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_trail WHERE trace_id = ? ORDER BY step ASC",
                (trace_id,),
            ).fetchall()
        return [AuditEntry.from_row(r) for r in rows]

    def query_recent(self, limit: int = 50) -> List[AuditEntry]:
        """查询最近 N 条记录。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_trail ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [AuditEntry.from_row(r) for r in rows]

    def query_by_action(self, action: str, limit: int = 50) -> List[AuditEntry]:
        """查询特定动作的记录。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_trail WHERE action = ? ORDER BY id DESC LIMIT ?",
                (action, limit),
            ).fetchall()
        return [AuditEntry.from_row(r) for r in rows]

    def query_by_phase(self, phase: str, limit: int = 50) -> List[AuditEntry]:
        """查询特定阶段的记录。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_trail WHERE phase = ? ORDER BY id DESC LIMIT ?",
                (phase, limit),
            ).fetchall()
        return [AuditEntry.from_row(r) for r in rows]

    def query_failures(self, limit: int = 50) -> List[AuditEntry]:
        """查询所有失败的记录。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_trail WHERE success = 0 ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [AuditEntry.from_row(r) for r in rows]

    def query_time_range(self, start: str, end: str, limit: int = 50) -> List[AuditEntry]:
        """查询时间范围内的记录。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_trail WHERE created_at BETWEEN ? AND ? ORDER BY id DESC LIMIT ?",
                (start, end, limit),
            ).fetchall()
        return [AuditEntry.from_row(r) for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        """获取审计统计摘要。"""
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
            successes = self._conn.execute(
                "SELECT COUNT(*) FROM audit_trail WHERE success = 1"
            ).fetchone()[0]
            failures = self._conn.execute(
                "SELECT COUNT(*) FROM audit_trail WHERE success = 0"
            ).fetchone()[0]

            # 按 action 分组
            action_counts = self._conn.execute(
                "SELECT action, COUNT(*) as cnt FROM audit_trail "
                "GROUP BY action ORDER BY cnt DESC LIMIT 10"
            ).fetchall()

        return {
            "total_entries": total,
            "successes": successes,
            "failures": failures,
            "success_rate": (successes / total * 100) if total > 0 else 0,
            "db_size_bytes": Path(self._conn.execute(
                "PRAGMA database_list"
            ).fetchone()[2]).stat().st_size if total > 0 else 0,
            "top_actions": [{"action": a, "count": c} for a, c in action_counts],
        }

    def replay_trace(self, trace_id: str) -> str:
        """以人类可读格式回放一个 trace 的完整执行链路。

        用于调试：精确还原「什么时间、谁、做了什么、成功与否」。
        """
        events = self.query_by_trace(trace_id)
        if not events:
            return f"No audit events found for trace_id={trace_id}"

        lines = [
            f"=== AUDIT REPLAY: {trace_id} ===",
            f"Total steps: {len(events)}",
            "",
        ]

        for e in events:
            icon = "+" if e.success else "✗"
            lines.append(
                f"[{icon}] Step {e.step} | {e.phase:12s} | {e.action:30s} | "
                f"{e.duration_ms:6.1f}ms"
            )
            if e.input_summary:
                lines.append(f"     IN:  {e.input_summary[:120]}")
            if e.output_summary:
                lines.append(f"     OUT: {e.output_summary[:120]}")
            if e.error_msg:
                lines.append(f"     ERR: {e.error_msg[:120]}")
            lines.append("")

        return "\n".join(lines)
