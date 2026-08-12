#!/usr/bin/env python3
"""一键运行自进化闭环（默认完全离线、确定性、可复现）。

    python scripts/run_self_evolve.py                     # 离线跑 5 轮 + 出看板
    python scripts/run_self_evolve.py --cycles 8 --seed 7
    python scripts/run_self_evolve.py --live --repo kiwoen/emperor-core   # 真实 PR

默认离线：执行用确定性基因驱动执行器（无需 LLM key、不连网），写回用
RecordingWriteChannel（只记录 PR 意图，绝不碰真实 git）。整条
「护栏→路由→执行→适应度→进化(熔断+晋升闸)→基准评测→评测闸→写回→可观测」
链路被真实跑通。``--live`` 才会切换为真实 GitWriteChannel（需 gh 凭据）。

运行后在 ``--out`` 目录产出：
  - ``run_report.json``  每轮功勋/成功率/评测/熔断/写回的完整记录
  - ``telemetry.json`` / ``telemetry.js`` / ``dashboard.html``  可观测看板
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
    default_ministers,
)
from jarvis.telemetry import MinisterTelemetry, collect, write_js, write_json  # noqa: E402


def _build_court() -> Court:
    cfg = CourtConfig(
        circuit_breaker=CircuitBreaker(CircuitConfig(
            drop_fraction=0.25, consecutive_negative=4, min_cycles_before_trip=2,
        )),
        promotion_gate=PromotionGate(PromotionGateConfig(
            required_consecutive_gains=2, min_merit=50.0,
        )),
        enable_auto_elimination=False,   # 安全默认：淘汰仍冻结（dry-run）
    )
    return Court(cfg)


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="一键运行自进化闭环（默认离线）")
    ap.add_argument("--cycles", type=int, default=5, help="进化轮数（默认 5）")
    ap.add_argument("--tasks-per-minister", type=int, default=2, help="每臣每轮任务数")
    ap.add_argument("--seed", type=int, default=0, help="确定性种子（可复现）")
    ap.add_argument("--out", default="telemetry", help="输出目录（默认 telemetry/）")
    ap.add_argument("--live", action="store_true", help="真实写回（需 gh 凭据）；默认离线记录")
    ap.add_argument("--repo", default="kiwoen/emperor-core", help="写回目标仓库")
    ap.add_argument("--no-writeback", action="store_true", help="禁用写回（仅跑进化+评测）")
    args = ap.parse_args(argv)

    court = _build_court()
    court.register_many(default_ministers())
    router, guardrail = _wire_optional_guards()

    # 写回通道：--no-writeback 禁用；--live 真实 GitWriteChannel；默认离线记录。
    if args.no_writeback:
        write_channel = None
    elif args.live:
        from jarvis.vcs.git_channel import GitWriteChannel
        write_channel = GitWriteChannel()
        print(f"🔴 LIVE 模式：将真实向 {args.repo} 开 PR（绝不直推 master）")
    else:
        write_channel = RecordingWriteChannel()

    engine = SelfEvolutionEngine(
        court=court,
        executor=GenomeDrivenExecutor(seed=args.seed),
        router=router,
        guardrail=guardrail,
        write_channel=write_channel,
        repo=args.repo,
    )

    report = engine.run(n_cycles=args.cycles, tasks_per_minister=args.tasks_per_minister)
    report.mode = "live" if args.live else "offline"

    # ── 产出：运行报告 + 可观测看板 ──
    os.makedirs(args.out, exist_ok=True)
    report_path = os.path.join(args.out, "run_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report.to_dict(), fh, ensure_ascii=False, indent=2)

    # 用最后一轮的评测做 telemetry（引擎内已跑过 canonical）
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
    write_json(snapshot, os.path.join(args.out, "telemetry.json"))
    write_js(snapshot, os.path.join(args.out, "telemetry.js"))
    # 复制自包含看板
    dash_src = os.path.join(os.path.dirname(__file__), "..", "dashboard.html")
    if os.path.exists(dash_src):
        import shutil
        shutil.copyfile(dash_src, os.path.join(args.out, "dashboard.html"))

    # ── 终端摘要 ──
    print("\n" + report.summary())
    print(f"\n✅ 产物已写入 {args.out}/ ：run_report.json · telemetry.json/js · dashboard.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
