"""
Human-In-The-Loop (HITL) Approval Engine — pre-execution gate for sensitive operations.

在 AI 自主执行高风险操作前，插入人工审批关卡。审批未通过 / 超时的一律禁止执行。

Architecture:
    ApprovalRequest — 单条审批请求（数据库映射）
    ApprovalPolicy   — 审批触发策略（按 domain/risk_level/capability/关键词）
    ApprovalEngine   — 审批引擎（创建、审批、拒绝、超时、策略管理）

Database schema:
    approval_requests (
        id            TEXT PRIMARY KEY,
        task_id       TEXT NOT NULL,
        prompt        TEXT NOT NULL,
        domain        TEXT NOT NULL,
        risk_level    TEXT NOT NULL,       -- "low" | "medium" | "high" | "critical"
        capability    TEXT,
        status        TEXT DEFAULT 'pending',  -- pending | approved | denied | timed_out
        requested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at   TIMESTAMP,
        approver_note TEXT,
        extra_json    TEXT                  -- 额外结构化数据
    )
    approval_policies (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_type     TEXT NOT NULL,        -- domain | risk_level | capability | keyword
        rule_value    TEXT NOT NULL,        -- 匹配值
        enabled       INTEGER DEFAULT 1,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )

Usage:
    from jarvis.approval import ApprovalEngine

    engine = ApprovalEngine("path/to/approval.db")
    need_approval = engine.require_approval(
        task_id="abc123", prompt="删除 C:\\Windows",
        domain="system", risk_level="critical", capability="file.delete"
    )
    if need_approval:
        req = engine.create_request(...)
        # ... show to user ...
        engine.approve(req.id, note="确认删除")
    else:
        # execute directly
        pass
"""

from __future__ import annotations

import json as _json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jarvis.approval")

# ── SQL Schema ──

SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_requests (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    domain        TEXT NOT NULL,
    risk_level    TEXT NOT NULL,
    capability    TEXT,
    status        TEXT DEFAULT 'pending',
    requested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at   TIMESTAMP,
    approver_note TEXT,
    extra_json    TEXT
);

CREATE TABLE IF NOT EXISTS approval_policies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type     TEXT NOT NULL,
    rule_value    TEXT NOT NULL,
    enabled       INTEGER DEFAULT 1,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_requests(status);
CREATE INDEX IF NOT EXISTS idx_approval_task_id ON approval_requests(task_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_rule ON approval_policies(rule_type, rule_value);
"""


# ── Dataclasses ──

@dataclass
class ApprovalRequest:
    """Single approval record."""
    id: str
    task_id: str
    prompt: str
    domain: str
    risk_level: str              # low | medium | high | critical
    capability: Optional[str] = None
    status: str = "pending"      # pending | approved | denied | timed_out
    requested_at: str = ""
    resolved_at: Optional[str] = None
    approver_note: Optional[str] = None
    extra_json: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "prompt": self.prompt,
            "domain": self.domain,
            "risk_level": self.risk_level,
            "capability": self.capability,
            "status": self.status,
            "requested_at": self.requested_at,
            "resolved_at": self.resolved_at,
            "approver_note": self.approver_note,
        }


@dataclass
class ApprovalPolicy:
    """Definition of a policy rule."""
    id: int = 0
    rule_type: str = ""   # domain | risk_level | capability | keyword
    rule_value: str = ""  # 匹配值
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "rule_type": self.rule_type,
            "rule_value": self.rule_value,
            "enabled": self.enabled,
        }


# ── Risk classifier ──

# 高危关键词自动升级 risk_level
CRITICAL_KEYWORDS = [
    "删除", "移除", "格式化", "清空", "重置",
    "delete", "remove", "format", "wipe", "reset",
    "注册表", "registry", "regedit",
    "rm -rf", "drop table", "truncate",
    "shutdown", "reboot", "restart",
    "chmod 777", "sudo",
]

HIGH_KEYWORDS = [
    "修改", "覆盖", "替换", "移动", "重命名",
    "modify", "overwrite", "replace", "move", "rename",
    "安装", "卸载", "install", "uninstall",
    "kill", "停止", "stop",
    "写入", "导出", "write", "export",
]

MEDIUM_KEYWORDS = [
    "下载", "download", "读取", "read", "查询",
    "打开", "open", "启动", "启动", "launch",
    "新建", "创建", "create", "new",
    "配置", "config", "设置", "set",
]


def classify_risk(prompt: str, domain: str = "general") -> str:
    """根据 prompt 内容和 domain 自动推断风险等级。"""
    pl = prompt.lower()

    for kw in CRITICAL_KEYWORDS:
        if kw in pl:
            return "critical"
    for kw in HIGH_KEYWORDS:
        if kw in pl:
            return "high"
    for kw in MEDIUM_KEYWORDS:
        if kw in pl:
            return "medium"

    if domain in ("system", "network", "registry"):
        return "high"
    if domain in ("finance", "payment", "auth"):
        return "critical"

    return "low"


# ── Approval Engine ──

class ApprovalEngine:
    """HITL approval manager with SQLite persistence and policy rules."""

    DEFAULT_TIMEOUT_MINUTES = 30  # 超时自动拒绝

    def __init__(
        self,
        db_path: str = "approval.db",
        timeout_minutes: int = 30,
        audit_logger: Any = None,
    ) -> None:
        self.db_path = str(db_path)
        self.timeout_minutes = timeout_minutes
        self._audit_logger = audit_logger
        self._lock = RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ── DB ──

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executescript(SCHEMA)
            conn.commit()

    # ── Policy ──

    def set_policy(self, rule_type: str, rule_value: str, enabled: bool = True) -> ApprovalPolicy:
        """Add or update a policy rule."""
        with self._lock:
            conn = self._get_conn()
            existing = conn.execute(
                "SELECT id FROM approval_policies WHERE rule_type=? AND rule_value=?",
                (rule_type, rule_value),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE approval_policies SET enabled=? WHERE id=?",
                    (1 if enabled else 0, existing["id"]),
                )
                policy_id = existing["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO approval_policies (rule_type, rule_value, enabled) VALUES (?, ?, ?)",
                    (rule_type, rule_value, 1 if enabled else 0),
                )
                policy_id = cur.lastrowid
            conn.commit()
        logger.debug("Policy %s=%s enabled=%s", rule_type, rule_value, enabled)
        return ApprovalPolicy(id=policy_id, rule_type=rule_type, rule_value=rule_value, enabled=enabled)

    def remove_policy(self, policy_id: int) -> bool:
        """Delete a policy rule by id."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM approval_policies WHERE id=?", (policy_id,))
            conn.commit()
        return cur.rowcount > 0

    def get_policies(self) -> List[ApprovalPolicy]:
        """返回所有审批策略（含禁用的）。"""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM approval_policies ORDER BY id").fetchall()
        return [
            ApprovalPolicy(
                id=r["id"],
                rule_type=r["rule_type"],
                rule_value=r["rule_value"],
                enabled=bool(r["enabled"]),
            )
            for r in rows
        ]

    def _match_policies(
        self, domain: str, risk_level: str, capability: Optional[str], prompt: str
    ) -> bool:
        """Check if any enabled policy matches the request params."""
        policies = self.get_policies()
        enabled = [p for p in policies if p.enabled]

        if not enabled:
            # 无策略时默认：critical / high 需要审批
            if risk_level in ("critical", "high"):
                return True
            return False

        for p in enabled:
            if p.rule_type == "domain" and p.rule_value == domain:
                return True
            if p.rule_type == "risk_level" and p.rule_value == risk_level:
                return True
            if p.rule_type == "capability" and capability and p.rule_value in (capability or ""):
                return True
            if p.rule_type == "keyword" and p.rule_value.lower() in prompt.lower():
                return True

        return False

    # ── Require check ──

    def require_approval(
        self,
        task_id: str,
        prompt: str,
        domain: str = "general",
        capability: Optional[str] = None,
    ) -> bool:
        """Check if this task requires human approval before execution."""
        risk_level = classify_risk(prompt, domain)
        needed = self._match_policies(domain, risk_level, capability, prompt)
        logger.debug(
            "Approval check: task=%s risk=%s domain=%s needed=%s",
            task_id, risk_level, domain, needed,
        )
        return needed

    # ── Request lifecycle ──

    def create_request(
        self,
        task_id: str,
        prompt: str,
        domain: str = "general",
        capability: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> ApprovalRequest:
        """Create a pending approval request. Call after require_approval returns True."""
        risk_level = classify_risk(prompt, domain)
        req_id = uuid.uuid4().hex[:12]
        now = datetime.utcnow().isoformat()

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO approval_requests
                   (id, task_id, prompt, domain, risk_level, capability, status, requested_at, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    req_id, task_id, prompt, domain, risk_level,
                    capability, now,
                    _json.dumps(extra) if extra else None,
                ),
            )
            conn.commit()

        req = ApprovalRequest(
            id=req_id, task_id=task_id, prompt=prompt,
            domain=domain, risk_level=risk_level,
            capability=capability, status="pending",
            requested_at=now,
        )

        if self._audit_logger is not None:
            try:
                self._audit_logger.log(
                    trace_id=req_id,
                    step=0, phase="approval",
                    action="approval.request",
                    input_summary=prompt[:200],
                    output_summary=f"pending — risk={risk_level}",
                    success=True, error_msg="",
                )
            except Exception:
                pass

        logger.info("Approval request created: %s (risk=%s)", req_id, risk_level)
        return req

    def approve(self, request_id: str, note: str = "") -> Optional[ApprovalRequest]:
        """Approve a pending request. Returns updated record or None."""
        return self._resolve(request_id, "approved", note)

    def deny(self, request_id: str, note: str = "") -> Optional[ApprovalRequest]:
        """Deny a pending request. Returns updated record or None."""
        return self._resolve(request_id, "denied", note)

    def _resolve(self, request_id: str, status: str, note: str) -> Optional[ApprovalRequest]:
        now = datetime.utcnow().isoformat()
        with self._lock:
            conn = self._get_conn()
            existing = conn.execute(
                "SELECT * FROM approval_requests WHERE id=? AND status='pending'",
                (request_id,),
            ).fetchone()
            if existing is None:
                logger.warning("Approval request %s not found or already resolved", request_id)
                return None

            conn.execute(
                "UPDATE approval_requests SET status=?, resolved_at=?, approver_note=? WHERE id=?",
                (status, now, note, request_id),
            )
            conn.commit()

        req = ApprovalRequest(
            id=request_id, task_id=existing["task_id"],
            prompt=existing["prompt"], domain=existing["domain"],
            risk_level=existing["risk_level"], capability=existing["capability"],
            status=status, requested_at=existing["requested_at"],
            resolved_at=now, approver_note=note,
        )

        if self._audit_logger is not None:
            try:
                self._audit_logger.log(
                    trace_id=request_id,
                    step=1, phase="approval",
                    action=f"approval.{status}",
                    input_summary=f"request={request_id}",
                    output_summary=f"{status} — {note}" if note else status,
                    success=(status == "approved"),
                    error_msg="" if status == "approved" else f"Denied: {note}",
                )
            except Exception:
                pass

        logger.info("Approval %s: %s (%s)", status, request_id, note)
        return req

    # ── Timeout sweep ──

    def sweep_timeouts(self) -> List[str]:
        """Auto-deny expired pending requests. Returns list of request IDs timed out."""
        cutoff = (datetime.utcnow() - timedelta(minutes=self.timeout_minutes)).isoformat()
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT id FROM approval_requests WHERE status='pending' AND requested_at <= ?",
                (cutoff,),
            ).fetchall()
            timed_out_ids = [r["id"] for r in rows]

            if timed_out_ids:
                now = datetime.utcnow().isoformat()
                conn.executemany(
                    "UPDATE approval_requests SET status='timed_out', resolved_at=? WHERE id=?",
                    [(now, rid) for rid in timed_out_ids],
                )
                conn.commit()

        if timed_out_ids:
            logger.info("Approval timeout auto-deny: %d requests", len(timed_out_ids))
        return timed_out_ids

    # ── Query ──

    def get_pending(self) -> List[ApprovalRequest]:
        """Returns all pending approval requests (sorted oldest first)."""
        self.sweep_timeouts()
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM approval_requests WHERE status='pending' ORDER BY requested_at ASC"
        ).fetchall()
        return [self._row_to_req(r) for r in rows]

    def get_by_id(self, request_id: str) -> Optional[ApprovalRequest]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_req(row)

    def get_history(self, limit: int = 50, offset: int = 0) -> List[ApprovalRequest]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM approval_requests WHERE status != 'pending' ORDER BY resolved_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_req(r) for r in rows]

    def count_pending(self) -> int:
        self.sweep_timeouts()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM approval_requests WHERE status='pending'"
        ).fetchone()
        return row["cnt"] if row else 0

    def _row_to_req(self, row) -> ApprovalRequest:
        return ApprovalRequest(
            id=row["id"],
            task_id=row["task_id"],
            prompt=row["prompt"],
            domain=row["domain"],
            risk_level=row["risk_level"],
            capability=row["capability"],
            status=row["status"],
            requested_at=row["requested_at"],
            resolved_at=row["resolved_at"],
            approver_note=row["approver_note"],
            extra_json=row["extra_json"],
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        self.close()
