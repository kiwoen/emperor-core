"""Tests for jarvis.approval — HITL Approval Engine."""

import os
import tempfile
import pytest
from pathlib import Path

from jarvis.approval import (
    ApprovalEngine,
    ApprovalRequest,
    ApprovalPolicy,
    classify_risk,
    CRITICAL_KEYWORDS,
)


# ── Helpers ──


@pytest.fixture
def engine():
    """Creates a temporary ApprovalEngine for isolated testing."""
    fd, tmp = tempfile.mkstemp(suffix=".db", prefix="approval_test_")
    os.close(fd)
    eng = ApprovalEngine(db_path=tmp, timeout_minutes=1)
    yield eng
    eng.close()
    try:
        os.unlink(tmp)
    except OSError:
        pass


# ── Risk classification ──


@pytest.mark.parametrize("prompt,domain,expected", [
    ("删除 C:\\Windows 中的文件", "system", "critical"),
    ("格式化硬盘", "storage", "critical"),
    ("修改注册表键值", "registry", "critical"),  # "注册表" in CRITICAL_KEYWORDS
    ("安装新软件包", "system", "high"),    # "安装" in HIGH_KEYWORDS
    ("查看当前天气", "general", "low"),
    ("rm -rf /tmp", "system", "critical"),
    ("shutdown now", "system", "critical"),
    ("读取配置文件", "general", "medium"),
    ("create a new file", "general", "medium"),
    ("hello world", "general", "low"),
])
def test_clasify_risk(prompt, domain, expected):
    assert classify_risk(prompt, domain) == expected


def test_classify_risk_domain_sensitive():
    """Domain names override keyword-less prompts."""
    assert classify_risk("list files", "finance") == "critical"
    assert classify_risk("list files", "system") == "high"
    assert classify_risk("list files", "network") == "high"
    assert classify_risk("list files", "general") == "low"


# ── Require approval (default policy) ──


def test_require_approval_default_critical(engine):
    """Without policies, critical/high always needs approval."""
    assert engine.require_approval("t1", "删除关键文件", "general") is True


def test_require_approval_default_high(engine):
    assert engine.require_approval("t2", "修改配置", "system") is True


def test_require_approval_default_low(engine):
    assert engine.require_approval("t3", "查看日志", "general") is False


def test_require_approval_default_medium(engine):
    assert engine.require_approval("t4", "读取文件内容", "general") is False


# ── Lifecycle: create → approve → verify ──


def test_create_and_approve(engine):
    assert engine.require_approval("t10", "删除数据库表 user_data", "general") is True
    req = engine.create_request("t10", "删除数据库表 user_data", "general")
    assert req.status == "pending"

    result = engine.approve(req.id, "确认可执行")
    assert result is not None
    assert result.status == "approved"
    assert result.approver_note == "确认可执行"

    # Verify not pending anymore
    pending = engine.get_pending()
    assert len(pending) == 0


def test_create_and_deny(engine):
    req = engine.create_request("t11", "格式化磁盘 E:", "storage")
    result = engine.deny(req.id, "不允许执行")
    assert result.status == "denied"
    assert engine.count_pending() == 0


def test_deny_twice_returns_none(engine):
    req = engine.create_request("t12", "reset 系统配置", "system")
    engine.deny(req.id)
    result2 = engine.deny(req.id)
    assert result2 is None


# ── Pending queue ──


def test_get_pending(engine):
    engine.create_request("t20", "删除日志", "general")
    engine.create_request("t21", "卸载应用", "system")
    pending = engine.get_pending()
    assert len(pending) == 2
    ids = [r.task_id for r in pending]
    assert "t20" in ids
    assert "t21" in ids


def test_count_pending(engine):
    assert engine.count_pending() == 0
    engine.create_request("t30", "高危操作", "system")
    assert engine.count_pending() == 1
    engine.create_request("t31", "中危操作", "general")
    assert engine.count_pending() == 2


def test_get_by_id(engine):
    req = engine.create_request("t40", "删除缓存", "general")
    found = engine.get_by_id(req.id)
    assert found is not None
    assert found.task_id == "t40"

    not_found = engine.get_by_id("nonexistent")
    assert not_found is None


# ── History ──


def test_get_history(engine):
    req1 = engine.create_request("t50", "任务A", "general")
    req2 = engine.create_request("t51", "任务B", "system")
    engine.approve(req1.id)
    engine.deny(req2.id)
    history = engine.get_history(limit=10)
    assert len(history) == 2
    statuses = {r.status for r in history}
    assert "approved" in statuses
    assert "denied" in statuses


# ── Policy management ──


def test_add_and_list_policies(engine):
    engine.set_policy("domain", "finance", enabled=True)
    engine.set_policy("risk_level", "critical", enabled=True)
    policies = engine.get_policies()
    assert len(policies) == 2
    types = {p.rule_type for p in policies}
    assert "domain" in types
    assert "risk_level" in types


def test_policy_idempotent_update(engine):
    engine.set_policy("keyword", "delete", enabled=True)
    engine.set_policy("keyword", "delete", enabled=False)
    policies = engine.get_policies()
    assert len(policies) == 1
    assert policies[0].enabled is False


def test_remove_policy(engine):
    policy = engine.set_policy("domain", "test", enabled=True)
    assert engine.remove_policy(policy.id) is True
    assert engine.remove_policy(99999) is False
    assert len(engine.get_policies()) == 0


def test_policy_triggers_approval(engine):
    """When a matching policy is added, task requires approval."""
    # Without policy: low risk doesn't need approval
    assert engine.require_approval("t60", "hello world", "general") is False
    # Add keyword policy
    engine.set_policy("keyword", "hello", enabled=True)
    assert engine.require_approval("t60", "hello world", "general") is True


def test_policy_domain_match(engine):
    engine.set_policy("domain", "finance", enabled=True)
    assert engine.require_approval("t70", "查询余额", "finance") is True
    assert engine.require_approval("t71", "查询余额", "general") is False  # low risk + no policy


def test_policy_capability_match(engine):
    engine.set_policy("capability", "file.delete", enabled=True)
    # But classify_risk checks keywords first -> "删除" hits critical
    assert engine.require_approval("t80", "删除文件", "general", "file.delete") is True


# ── Timeout sweep ──


def test_sweep_timeouts(engine):
    # Artificially force a very short timeout
    engine.timeout_minutes = 0  # immediate timeout
    req = engine.create_request("t90", "过时任务", "general")
    engine.sweep_timeouts()
    found = engine.get_by_id(req.id)
    assert found.status == "timed_out"


# ── Integration: execution flow ──


def test_full_approval_flow(engine):
    """Simulate the complete approve-then-execute flow."""
    # 1. Check
    assert engine.require_approval("t100", "删除关键数据", "db") is True
    # 2. Create
    req = engine.create_request("t100", "删除关键数据", "db")
    assert req.status == "pending"
    assert req.risk_level == "critical"
    # 3. Pending visible
    pending = engine.get_pending()
    assert len(pending) == 1
    assert pending[0].id == req.id
    # 4. Approve
    result = engine.approve(req.id, "审批通过")
    assert result.status == "approved"
    # 5. No longer pending
    assert engine.count_pending() == 0
    # 6. History records
    history = engine.get_history()
    assert len(history) == 1
    assert history[0].status == "approved"


def test_to_dict_contains_keys(engine):
    req = engine.create_request("t110", "测试任务", "general")
    d = req.to_dict()
    for key in ("id", "task_id", "prompt", "domain", "risk_level", "status"):
        assert key in d
