"""approval_gate 的单元测试：把自进化写回接入既有 ApprovalEngine 人类审批门。"""

from __future__ import annotations

import os
import tempfile

from jarvis.approval import ApprovalEngine
from jarvis.court.approval_gate import WritebackApprovalGate


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="approval_test_")
    os.close(fd)
    os.remove(path)  # 让引擎自己建库
    return path


def _safe_remove(path: str) -> None:
    """Windows 下 sqlite WAL 可能仍锁文件，忽略删除失败。"""
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def test_no_engine_falls_back_to_approved():
    gate = WritebackApprovalGate(approval_engine=None, auto_approve=False)
    out = gate.decide("repo/x", "master", "diff...", cycle=1)
    assert out.status == "approved"
    assert out.request_id is None


def test_engine_auto_approve_creates_approved_request():
    db = _tmp_db()
    engine = ApprovalEngine(db)
    try:
        gate = WritebackApprovalGate(engine, auto_approve=True)
        out = gate.decide("repo/x", "master", "diff...", cycle=2, title="t")
        assert out.status == "approved"
        assert out.request_id is not None
        req = engine.get_by_id(out.request_id)
        assert req is not None and req.status == "approved"
    finally:
        engine.close()
        _safe_remove(db)


def test_engine_manual_approval_pending():
    db = _tmp_db()
    engine = ApprovalEngine(db)
    try:
        gate = WritebackApprovalGate(engine, auto_approve=False)
        out = gate.decide("repo/x", "master", "diff...", cycle=3, title="t")
        assert out.status == "pending"
        assert out.request_id is not None
        req = engine.get_by_id(out.request_id)
        assert req is not None and req.status == "pending"
        # 自我改写代码属于高风险操作（critical / high），必须进审批门
        assert req.risk_level in ("critical", "high")
    finally:
        engine.close()
        _safe_remove(db)
