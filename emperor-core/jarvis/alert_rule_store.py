"""In-memory alert rules store.

Stores alert rules with rule_id / name / condition / threshold / severity / enabled / created_at.
Provides query, toggle, create, and delete operations. Preloaded with 5 sample rules for
dashboard demonstration.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

MAX_RULES = 100


class AlertRuleStore:
    """Thread-safe in-memory store for alert rules."""

    def __init__(self) -> None:
        self._rules: list[dict] = []
        self._lock = threading.Lock()
        self._seed_sample_rules()

    def _seed_sample_rules(self) -> None:
        """Pre-populate with 5 sample alert rules for dashboard display."""
        samples = [
            {
                "rule_id": "ar_task_failure_rate",
                "name": "任务失败率过高",
                "condition": "当 任务失败率 > 10%",
                "threshold": 10.0,
                "severity": "critical",
                "enabled": True,
                "created_at": time.time() - 86400 * 7,
            },
            {
                "rule_id": "ar_minister_timeout",
                "name": "Minister 响应超时",
                "condition": "当 Minister 响应时间 > 5s",
                "threshold": 5.0,
                "severity": "warning",
                "enabled": True,
                "created_at": time.time() - 86400 * 5,
            },
            {
                "rule_id": "ar_memory_usage",
                "name": "内存使用率过高",
                "condition": "当 内存使用率 > 85%",
                "threshold": 85.0,
                "severity": "warning",
                "enabled": True,
                "created_at": time.time() - 86400 * 3,
            },
            {
                "rule_id": "ar_confidence_drop",
                "name": "置信度骤降",
                "condition": "当 Minister 平均置信度 < 0.6",
                "threshold": 0.6,
                "severity": "critical",
                "enabled": False,
                "created_at": time.time() - 86400 * 2,
            },
            {
                "rule_id": "ar_pipeline_queue",
                "name": "Pipeline 队列积压",
                "condition": "当 队列积压 > 100 条",
                "threshold": 100.0,
                "severity": "info",
                "enabled": True,
                "created_at": time.time() - 86400,
            },
        ]
        self._rules = samples

    # ── CRUD ──

    def get_all(self) -> list[dict]:
        """Return all rules, newest first."""
        with self._lock:
            return sorted(self._rules, key=lambda r: r["created_at"], reverse=True)

    def get_by_id(self, rule_id: str) -> Optional[dict]:
        """Return a single rule by rule_id, or None."""
        with self._lock:
            for r in self._rules:
                if r["rule_id"] == rule_id:
                    return dict(r)
        return None

    def add(self, name: str, condition: str, threshold: float, severity: str) -> dict:
        """Create a new rule. Returns the newly-created record."""
        if severity not in ("critical", "warning", "info"):
            raise ValueError(
                f"Invalid severity '{severity}', must be critical/warning/info"
            )

        rule_id = f"ar_{uuid.uuid4().hex[:12]}"
        record = {
            "rule_id": rule_id,
            "name": name,
            "condition": condition,
            "threshold": threshold,
            "severity": severity,
            "enabled": True,
            "created_at": time.time(),
        }
        with self._lock:
            self._rules.append(record)
            if len(self._rules) > MAX_RULES:
                self._rules = self._rules[-MAX_RULES:]
        return record

    def toggle(self, rule_id: str) -> Optional[dict]:
        """Toggle enabled/disabled. Returns updated rule or None if not found."""
        with self._lock:
            for r in self._rules:
                if r["rule_id"] == rule_id:
                    r["enabled"] = not r["enabled"]
                    return dict(r)
        return None

    def delete(self, rule_id: str) -> bool:
        """Delete a rule by rule_id. Returns True if deleted."""
        with self._lock:
            for i, r in enumerate(self._rules):
                if r["rule_id"] == rule_id:
                    self._rules.pop(i)
                    return True
        return False

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._rules)

    def clear(self) -> None:
        """Remove all stored rules (mainly for tests)."""
        with self._lock:
            self._rules.clear()


# Module-level singleton
alert_rule_store = AlertRuleStore()
