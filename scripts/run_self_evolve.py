#!/usr/bin/env python3
"""一键运行自进化闭环（默认完全离线、确定性、可复现）。

    python scripts/run_self_evolve.py                          # 离线跑 5 轮 + 出看板
    python scripts/run_self_evolve.py --cycles 8 --seed 7
    python scripts/run_self_evolve.py --config configs/self_evolve.yaml
    python scripts/run_self_evolve.py --approval --auto-approve  # 接入人类审批门(离线自动批)
    python scripts/run_self_evolve.py --audit                   # 写入不可篡改审计库
    python scripts/run_self_evolve.py --live --repo kiwoen/emperor-core   # 真实 PR

默认离线：执行用确定性基因驱动执行器（无需 LLM key、不连网），写回用
RecordingWriteChannel（只记录 PR 意图，绝不碰真实 git）。整条
「护栏→路由→执行→适应度→进化(熔断+晋升闸)→基准评测→评测闸→人类审批闸→写回→可观测」
链路被真实跑通，且写回携带**系统这一轮对自己基因的真实 diff**（可审查、可回滚）。
``--live`` 才会切换为真实 GitWriteChannel（需 gh 凭据 + 人类在 GitHub 上 review）。

运行后在 ``--out`` 目录产出：
  - ``run_report.json``  每轮功勋/成功率/评测/熔断/写回的完整记录
  - ``telemetry.json`` / ``telemetry.js`` / ``dashboard.html``  可观测看板
  - （若 --resume 或正常结束）``jarvis/court/genome_state.json`` 基因检查点
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jarvis.court.circuit_breaker import (  # noqa: E402
    CircuitBreaker, CircuitConfig, PromotionGate, PromotionGateConfig,
)
from jarvis.court.court import Court, CourtConfig  # noqa: E402
from jarvis.self_evolve import (  # noqa: E402
    GenomeDrivenExecutor, RecordingWriteChannel, SelfEvolutionEngine,
    SimulatedTask, default_ministers, default_tasks, real_default_tasks,
)
from jarvis.self_evolve_config import SelfEvolveConfig, load_config  # noqa: E402
from jarvis.telemetry import MinisterTelemetry, collect, write_js, write_json  # noqa: E402


def _build_court(cfg: SelfEvolveConfig) -> Court:
    cc = cfg.circuit_breaker or {}
    pg = cfg.promotion_gate or {}
    court_cfg = CourtConfig(
        circuit_breaker=CircuitBreaker(CircuitConfig(
            drop_fraction=float(cc.get("drop_fraction", 0.25)),
            consecutive_negative=int(cc.get("consecutive_negative", 4)),
            min_cycles_before_trip=int(cc.get("min_cycles_before_trip", 2)),
        )),
        promotion_gate=PromotionGate(PromotionGateConfig(
            required_consecutive_gains=int(pg.get("required_consecutive_gains", 2)),
            min_merit=float(pg.get("min_merit", 50.0)),
        )),
        enable_auto_elimination=bool(cfg.enable_auto_elimination),
    )
    return Court(court_cfg)


def _wire_optional_guards():
    """装配护栏与路由（离线安全；任一不可用则降级为 None）。"""
    router = guardrail = None
    try:
        from jarvis.model_router import SmartRouter
        router = SmartRouter()
    except Exception:  # pragma: no cover - 可选组件
        print("⚠️  SmartRouter 不可用，路由观测跳过")
    try:
        from jarvis.guardrail_chain import GuardrailChain
        guardrail = GuardrailChain()   # 无重型守卫 → shadow 模式降级
    except Exception:  # pragma: no cover
        print("⚠️  GuardrailChain 不可用，护栏观测跳过")
    return router, guardrail


def _minister_merit(m: Any) -> float:
    for attr in ("windowed_merit", "merit_score", "merit"):
        v = getattr(m, attr, None)
        if v is not None:
            return float(v)
    return 0.0


def _court_ministers(court: Court):
    genomes = getattr(court._sm, "_genomes", {})
    statuses = getattr(court._sm, "_statuses", {})
    out = []
    for m in court.merit_ranking:
        name = getattr(m, "name", None) or getattr(m, "minister", "?")
        g = genomes.get(name)
        st = statuses.get(name)
        out.append(MinisterTelemetry(
            name=name,
            domain=getattr(g, "domain", "general") if g else "general",
            merit=_minister_merit(m),
            status=getattr(st, "value", str(st)) if st else "active",
            success_streak=int(getattr(g, "success_streak", 0)) if g else 0,
            failure_streak=int(getattr(g, "failure_streak", 0)) if g else 0,
        ))
    return out


def _build_write_channel(mode: str, live: bool, repo: str):
    """返回写回通道：record(离线记录)/none(禁用)/live(真实 PR)。"""
    if mode == "none" or (not live and mode == "record"):
        if not live and mode != "none":
            return RecordingWriteChannel()
        if mode == "none":
            return None
    if live or mode == "live":
        from jarvis.vcs.git_channel import GitWriteChannel
        print(f"🔴 LIVE 模式：将真实向 {repo} 开 PR（绝不直推 master）")
        return GitWriteChannel()
    # 默认：离线记录
    return RecordingWriteChannel()


def _build_executor(cfg: SelfEvolveConfig, memory=None):
    """按配置选执行器。返回 (executor, is_real)。

    sim  → GenomeDrivenExecutor（基因质量直接伪造信号，旧演示路径）；
    real → RealTaskExecutor（真实离线求解 / 真实 LLM，基因门控真实对错）——默认；
    auto → 同 real（「执行任务」为本项目目标，默认走真实执行）。
    """
    mode = (cfg.executor or "auto").strip().lower()
    if mode == "sim":
        return GenomeDrivenExecutor(seed=cfg.seed), False
    from jarvis.court.real_executor import RealTaskExecutor
    return RealTaskExecutor(seed=cfg.seed, memory=memory), True


def _load_tasks(cfg: SelfEvolveConfig, is_real: bool) -> list:
    """加载任务：内联 tasks > task_file(YAML/JSON) > 真实默认任务(real) / 占位任务(sim)。"""
    raw: list = list(cfg.tasks or [])
    if not raw and cfg.task_file and os.path.exists(cfg.task_file):
        try:
            with open(cfg.task_file, "r", encoding="utf-8") as fh:
                text = fh.read()
            if cfg.task_file.endswith((".yaml", ".yml")):
                import yaml  # type: ignore
                raw = yaml.safe_load(text) or []
            else:
                raw = json.loads(text or "[]")
        except Exception as exc:
            print(f"⚠️  任务文件 {cfg.task_file} 解析失败（{exc}），退回默认任务")
            raw = []
    if raw:
        out = []
        for i, t in enumerate(raw):
            out.append(SimulatedTask(
                id=str(t.get("id", f"task-{i:02d}")),
                prompt=str(t.get("prompt", "")),
                domain=str(t.get("domain", "general")),
                expected=(t.get("expected") if t.get("expected") is not None else None),
            ))
        return out
    return real_default_tasks() if is_real else default_tasks()


def run_orchestrator(cfg: SelfEvolveConfig, out_dir: str = "telemetry",
                     live: bool = False, no_writeback: bool = False) -> int:
    """按配置构建并运行自进化闭环，产出报告 + 看板。"""
    court = _build_court(cfg)
    court.register_many(default_ministers())
    router, guardrail = _wire_optional_guards()

    # 经验记忆（Phase 12：自我学习跨重启累积，executor 与 engine 共享同一实例）
    memory = None
    if cfg.use_memory:
        from jarvis.court.memory import CourtMemory
        memory = CourtMemory()

    # 真实任务执行器 + 任务集（Phase 11：真实执行 + 自我学习）
    executor, is_real = _build_executor(cfg, memory=memory)
    tasks = _load_tasks(cfg, is_real)
    self_learn = bool(cfg.self_learn and is_real)
    mode_name = "real" if is_real else "sim"
    print(f"🧠 执行器={mode_name} 自我学习={'开' if self_learn else '关'} "
          f"经验记忆={'开' if memory is not None else '关'} 任务数={len(tasks)}")

    # 写回通道
    if no_writeback:
        write_channel = None
    else:
        mode = "live" if live else cfg.writeback
        write_channel = _build_write_channel(mode, live, cfg.repo)

    # 人类审批门（接入既有 ApprovalEngine）
    approval_engine = None
    if cfg.use_approval_engine:
        from jarvis.approval import ApprovalEngine
        approval_engine = ApprovalEngine("approval.db")
        print(f"🔐 已接入人类审批门（auto_approve={cfg.auto_approve}）")

    # 不可篡改审计
    audit_logger = None
    if cfg.use_audit:
        from jarvis.audit import AuditLogger
        audit_logger = AuditLogger("audit.db")
        print("📜 已接入不可篡改审计库 audit.db")

    # 评测闸
    wg = cfg.write_gate or {}
    from jarvis.vcs.writeback_gate import WritebackGate
    write_gate = WritebackGate(
        min_pass_rate=float(wg.get("min_pass_rate", 0.0)),
        forbid_regression=bool(wg.get("forbid_regression", False)),
    )

    # 金标准安全闸（Phase 9：fail-closed，写回前最后一道硬约束）
    safety_gate = None
    if cfg.use_safety_gate:
        from jarvis.court.safety_gate import default_safety_gate
        core = cfg.core_ministers if cfg.core_ministers else ("math_alpha", "reason_gamma")
        safety_gate = default_safety_gate(
            quality_floor=cfg.quality_floor,
            core_ministers=core,
            max_regression=cfg.max_regression,
            golden_pass_rate_min=cfg.golden_pass_rate_min,
        )
        print(f"🛡️  已启用金标准安全闸（core={list(core)} 质量地板={cfg.quality_floor} "
              f"最大回退={cfg.max_regression} 行为地板={cfg.golden_pass_rate_min}）")

    engine = SelfEvolutionEngine(
        court=court,
        executor=executor,
        tasks=tasks,
        router=router,
        guardrail=guardrail,
        write_channel=write_channel,
        write_gate=write_gate,
        repo=cfg.repo,
        approval_engine=approval_engine,
        auto_approve=cfg.auto_approve,
        audit_logger=audit_logger,
        genome_state_path=cfg.genome_state_path,
        resume=cfg.resume,
        safety_gate=safety_gate,
        use_safety_gate=cfg.use_safety_gate,
        resource_seconds=cfg.resource_seconds,
        resource_max_ops=cfg.resource_max_ops,
        enable_snapshots=cfg.enable_snapshots,
        snapshot_dir=cfg.snapshot_dir,
        golden_pass_rate_min=cfg.golden_pass_rate_min,
        self_learn=self_learn,
        memory=memory,
        use_memory=cfg.use_memory,
        memory_path=cfg.memory_path,
        warm_start_from_memory=cfg.warm_start_from_memory,
    )

    report = engine.run(n_cycles=cfg.cycles, tasks_per_minister=cfg.tasks_per_minister)
    report.mode = "live" if live else "offline"

    # ── 产出：运行报告 + 可观测看板 ──
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "run_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report.to_dict(), fh, ensure_ascii=False, indent=2)

    final_eval = None
    if report.cycles and report.cycles[-1].eval_pass_rate is not None:
        from jarvis.eval_bench.criteria import EvalReport as _ER
        last = report.cycles[-1]
        total = len(getattr(engine.eval_suite, "cases", []))
        passed = round(last.eval_pass_rate * total)
        final_eval = _ER(cases=total, passed=passed, failed=total - passed,
                         pass_rate=last.eval_pass_rate)
    snapshot = collect(
        circuit_breaker=getattr(court, "_circuit_breaker", None),
        eval_report=final_eval,
        ministers=_court_ministers(court),
        evolution_events=[vars(c) for c in report.cycles],
    )
    write_json(snapshot, os.path.join(out_dir, "telemetry.json"))
    write_js(snapshot, os.path.join(out_dir, "telemetry.js"))
    dash_src = os.path.join(os.path.dirname(__file__), "..", "dashboard.html")
    if os.path.exists(dash_src):
        import shutil
        shutil.copyfile(dash_src, os.path.join(out_dir, "dashboard.html"))

    print("\n" + report.summary())
    print(f"\n✅ 产物已写入 {out_dir}/ ：run_report.json · telemetry.json/js · dashboard.html")
    if write_channel is not None:
        proposed = [c for c in report.cycles if str(c.writeback).startswith("proposed")]
        pending = [c for c in report.cycles if str(c.writeback).startswith("pending-approval")]
        if proposed:
            print(f"📦 本轮回写携带真实基因 diff 的 PR 意图 {len(proposed)} 次（见 telemetry 与 genome_state.json）")
        if pending:
            print(f"⏳ {len(pending)} 次写回进入「待人工审批」状态（approval.db 中可查）")

    # 经验记忆可观测：把「学到了什么」按域打印出来（跨重启累积的证据）
    stats = engine.memory_stats()
    if stats:
        print(f"\n🧠 经验记忆已落盘 {cfg.memory_path}（跨重启累积，--resume 继续学习）：")
        for s in stats[:8]:
            print(f"   · {s['domain']:<10} 样本={s['total']:<4} 成功率={s['success_rate']:.0%} "
                  f"最强大臣={s.get('top_minister') or '-'}")
    return 0


def run_safety_check(path: str, core_ministers=("math_alpha", "reason_gamma"),
                     quality_floor: float = 0.05, max_regression: float = 0.10) -> int:
    """对当前基因快照跑金标准安全闸，返回退出码（0=通过，1=拒绝）。"""
    from jarvis.court.genome_store import GenomeStore
    from jarvis.court.safety_gate import SafetyContext, default_safety_gate

    genomes, meta = GenomeStore.load(path)
    if not genomes:
        print(f"⚠️  未找到基因快照 {path}，跳过安全校验")
        return 0
    payload = {
        "version": 1, "metadata": meta,
        "genomes": [GenomeStore.to_dict(g) for g in genomes],
    }
    gate = default_safety_gate(
        quality_floor=quality_floor, core_ministers=core_ministers,
        max_regression=max_regression)
    report = gate.run(SafetyContext(before={}, after=payload))
    print(report.summary())
    return 0 if report.passed else 1


def run_rollback(snapshot_dir: str, snapshot_id: str = "", list_only: bool = False,
                genome_state_path: str = "jarvis/court/genome_state.json") -> int:
    """列出或执行基因快照回滚。"""
    from jarvis.court.court import Court, CourtConfig
    from jarvis.court.rollback import RollbackManager

    mgr = RollbackManager(snapshot_dir=snapshot_dir, genome_state_relpath=genome_state_path)
    if list_only or not snapshot_id:
        rows = mgr.list()
        if not rows:
            print("（无快照）")
            return 0 if list_only else 2
        for m in rows:
            tag = "🔒" if m.safe else "  "
            print(f"{tag} {m.id}  cycle={m.cycle}  label={m.label}  {m.timestamp}")
        return 0 if list_only else 2

    court = Court(CourtConfig())
    ok = mgr.rollback_to(snapshot_id, court, genome_state_path)
    print(f"{'✅' if ok else '❌'} 回滚到 {snapshot_id}：" + ("成功" if ok else "失败（快照不存在或载入异常）"))
    if ok:
        print(f"   已恢复基因并同步 {genome_state_path}（可立即 --resume 续跑）")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="一键运行自进化闭环（默认离线）")
    ap.add_argument("--config", type=str, default=None, help="YAML 配置文件路径")
    ap.add_argument("--cycles", type=int, default=None, help="进化轮数")
    ap.add_argument("--tasks-per-minister", type=int, default=None, help="每臣每轮任务数")
    ap.add_argument("--seed", type=int, default=None, help="确定性种子（可复现）")
    ap.add_argument("--out", default="telemetry", help="输出目录（默认 telemetry/）")
    ap.add_argument("--live", action="store_true", help="真实写回（需 gh 凭据）；默认离线记录")
    ap.add_argument("--repo", default=None, help="写回目标仓库")
    ap.add_argument("--no-writeback", action="store_true", help="禁用写回（仅跑进化+评测）")
    ap.add_argument("--resume", action="store_true", help="从基因检查点续跑")
    ap.add_argument("--approval", action="store_true", help="接入人类审批门（ApprovalEngine）")
    ap.add_argument("--auto-approve", action="store_true", help="审批门自动批准（仅离线/CI）")
    ap.add_argument("--audit", action="store_true", help="写入不可篡改审计库 audit.db")
    args = ap.parse_args(argv)

    # 配置：先加载 YAML，再用命令行覆盖
    cfg = load_config(args.config) if args.config else SelfEvolveConfig()
    if args.cycles is not None:
        cfg.cycles = args.cycles
    if args.tasks_per_minister is not None:
        cfg.tasks_per_minister = args.tasks_per_minister
    if args.seed is not None:
        cfg.seed = args.seed
    if args.repo is not None:
        cfg.repo = args.repo
    if args.resume:
        cfg.resume = True
    if args.approval:
        cfg.use_approval_engine = True
    if args.auto_approve:
        cfg.auto_approve = True
        cfg.use_approval_engine = True
    if args.audit:
        cfg.use_audit = True

    return run_orchestrator(cfg, out_dir=args.out, live=args.live,
                            no_writeback=args.no_writeback)


if __name__ == "__main__":
    sys.exit(main() or 0)
