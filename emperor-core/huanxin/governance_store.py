"""In-memory governance rules store.

Stores governance rules with rule_id / description / priority / enabled / created_at / remediation.
Provides query, toggle, create, and delete operations. Preloaded with 5 sample rules for
dashboard demonstration.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

MAX_RULES = 100


class GovernanceStore:
    """Thread-safe in-memory store for governance rules."""

    def __init__(self) -> None:
        self._rules: list[dict] = []
        self._lock = threading.Lock()
        self._seed_sample_rules()

    def _seed_sample_rules(self) -> None:
        """Pre-populate with 5 sample governance rules for dashboard display."""
        samples = [
            {
                "rule_id": "gov_no_pii_output",
                "description": "禁止在 LLM 输出中包含个人身份信息（PII）：身份证号、手机号、银行卡号等",
                "priority": "P0",
                "enabled": True,
                "created_at": time.time() - 86400 * 7,
                "remediation": "自动对输出内容进行 PII 脱敏处理，将敏感字段替换为 [REDACTED]",
            },
            {
                "rule_id": "gov_rbac_admin_only",
                "description": "仅管理员角色可执行系统级操作：修改配置、删除大臣、变更调度策略",
                "priority": "P0",
                "enabled": True,
                "created_at": time.time() - 86400 * 5,
                "remediation": "非管理员尝试时自动拒绝并记录审计日志，通知管理员复核",
            },
            {
                "rule_id": "gov_gdpr_data_export",
                "description": "数据导出操作需通过 GDPR 合规审查，禁止批量导出含用户隐私的数据集",
                "priority": "P1",
                "enabled": True,
                "created_at": time.time() - 86400 * 3,
                "remediation": "导出请求自动路由到审批引擎，需数据保护官 (DPO) 批准后执行",
            },
            {
                "rule_id": "gov_api_rate_limit",
                "description": "单 Minister 每分钟 API 调用上限 60 次，超限自动熔断并降级",
                "priority": "P2",
                "enabled": False,
                "created_at": time.time() - 86400 * 2,
                "remediation": "触发熔断后进入冷却期 120 秒，期间请求排队等待或降级到缓存响应",
            },
            {
                "rule_id": "gov_code_review_mandatory",
                "description": "所有代码生成任务在提交前须通过安全审查：禁止 eval/exec/os.system 调用",
                "priority": "P1",
                "enabled": True,
                "created_at": time.time() - 86400,
                "remediation": "生成代码自动跑安全扫描，命中危险模式则阻止提交并提示修改方案",
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

    def add(self, description: str, priority: str, remediation: str = "") -> dict:
        """Create a new rule. Returns the newly-created record."""
        if priority not in ("P0", "P1", "P2", "P3"):
            raise ValueError(f"Invalid priority '{priority}', must be P0-P3")

        rule_id = f"gov_{uuid.uuid4().hex[:12]}"
        record = {
            "rule_id": rule_id,
            "description": description,
            "priority": priority,
            "enabled": True,
            "created_at": time.time(),
            "remediation": remediation,
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
governance_store = GovernanceStore()
