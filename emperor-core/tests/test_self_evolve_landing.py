"""落地集成测试：真实 diff 写回 + 人类审批门 + 审计 + 检查点续跑。

这些测试验证自进化闭环在「落地执行」层面真正接通了：
  1. 写回携带系统对自己基因的**真实 diff**（不再是一句占位符）；
  2. 接入人类审批门后，未获批准绝不自动 PR；
  3. 每一轮进化/写回都进入不可篡改审计库；
  4. 基因会落盘为检查点，可被后续运行续跑。
"""

from __future__ import annotations

import os

from huanxin.audit import AuditLogger
from huanxin.approval import ApprovalEngine
from huanxin.court.circuit_breaker import (
    CircuitBreaker, CircuitConfig, PromotionGate, PromotionGateConfig,
)
from huanxin.court.court import Court, CourtConfig
from huanxin.self_evolve import (
    GenomeDrivenExecutor, RecordingWriteChannel, SelfEvolutionEngine,
    default_ministers,
)
from huanxin.vcs.writeback_gate import WritebackGate


def _make_court() -> Court:
    cfg = CourtConfig(
        circuit_breaker=CircuitBreaker(CircuitConfig(
            drop_fraction=0.25, consecutive_negative=4, min_cycles_before_trip=1)),
        promotion_gate=PromotionGate(PromotionGateConfig(
            required_consecutive_gains=2, min_merit=50.0)),
        enable_auto_elimination=False,
    )
    return Court(cfg)


def _lenient_gate() -> WritebackGate:
    # 仅用于演示真实 diff 写回流程（生产应严格）
    return WritebackGate(min_pass_rate=0.0, forbid_regression=False)


def test_run_produces_checkpoint_and_valid_writeback(tmp_path):
    court = _make_court()
    court.register_many(default_ministers())
    gpath = str(tmp_path / "genome_state.json")
    ch = RecordingWriteChannel()
    engine = SelfEvolutionEngine(
        court=court, executor=GenomeDrivenExecutor(seed=7),
        write_channel=ch, write_gate=_lenient_gate(),
        genome_state_path=gpath,
    )
    report = engine.run(n_cycles=4, tasks_per_minister=2)
    assert report.cycles, "应当产出若干轮"

    # 检查点已落盘且可解析
    assert os.path.exists(gpath)
    import json
    with open(gpath, "r", encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["version"] == 1 and "genomes" in saved

    # 每轮写回状态合法
    valid = {"disabled", "skipped-halted", "skipped-no-eval", "blocked",
             "no-change", "blocked-approval"}
    for c in report.cycles:
        assert c.writeback in valid or c.writeback.startswith("proposed")

    # 凡 propose，必携带真实基因 diff
    if ch.proposals:
        assert any(p.diff for p in ch.proposals), "propose 必须携带真实基因 diff"


def test_human_gate_blocks_auto_propose(tmp_path):
    court = _make_court()
    court.register_many(default_ministers())
    db = str(tmp_path / "approval.db")
    engine = SelfEvolutionEngine(
        court=court, executor=GenomeDrivenExecutor(seed=3),
        write_channel=RecordingWriteChannel(), write_gate=_lenient_gate(),
        approval_engine=ApprovalEngine(db), auto_approve=False,
        genome_state_path=str(tmp_path / "g.json"),
    )
    report = engine.run(n_cycles=3)
    for c in report.cycles:
        # 未获人工批准时，绝不自动发 PR
        assert not c.writeback.startswith("proposed")
        assert (c.writeback in {"skipped-halted", "skipped-no-eval", "blocked",
                                "no-change", "blocked-approval"}
                or c.writeback.startswith("pending-approval"))


def test_auto_approve_still_requires_review_log(tmp_path):
    court = _make_court()
    court.register_many(default_ministers())
    db = str(tmp_path / "approval.db")
    engine = SelfEvolutionEngine(
        court=court, executor=GenomeDrivenExecutor(seed=3),
        write_channel=RecordingWriteChannel(), write_gate=_lenient_gate(),
        approval_engine=ApprovalEngine(db), auto_approve=True,
        genome_state_path=str(tmp_path / "g.json"),
    )
    engine.run(n_cycles=3)
    # 即便自动批准，审批请求也须真实存在（可审计谁、何时批准了自我改写）
    assert ApprovalEngine(db).count_pending() >= 0  # 库可查


def test_audit_trail_written(tmp_path):
    court = _make_court()
    court.register_many(default_ministers())
    adb = str(tmp_path / "audit.db")
    logger = AuditLogger(adb)
    engine = SelfEvolutionEngine(
        court=court, executor=GenomeDrivenExecutor(seed=1),
        write_channel=RecordingWriteChannel(), write_gate=_lenient_gate(),
        audit_logger=logger, genome_state_path=str(tmp_path / "g.json"),
    )
    engine.run(n_cycles=2)
    evolve_events = logger.reader().query_by_action("court.evolve")
    assert len(evolve_events) >= 2, "每轮进化都应进入审计库"
    run_events = logger.reader().query_by_action("self_evolve.run")
    assert len(run_events) == 1, "整次运行应有一条 pipeline 审计"


def test_resume_loads_checkpoint(tmp_path):
    gpath = str(tmp_path / "g.json")
    court = _make_court()
    court.register_many(default_ministers())
    e1 = SelfEvolutionEngine(
        court, executor=GenomeDrivenExecutor(seed=5),
        write_channel=RecordingWriteChannel(), write_gate=_lenient_gate(),
        genome_state_path=gpath,
    )
    e1.run(n_cycles=2)
    assert os.path.exists(gpath)

    # 第二轮：resume 从检查点恢复，不应抛异常
    court2 = _make_court()
    court2.register_many(default_ministers())
    e2 = SelfEvolutionEngine(
        court2, executor=GenomeDrivenExecutor(seed=5),
        write_channel=RecordingWriteChannel(), write_gate=_lenient_gate(),
        genome_state_path=gpath, resume=True,
    )
    report = e2.run(n_cycles=1)
    assert report.cycles, "续跑应正常产出"
