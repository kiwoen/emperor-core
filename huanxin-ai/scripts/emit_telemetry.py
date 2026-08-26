#!/usr/bin/env python3
"""生成自进化系统可观测性快照（telemetry.json + telemetry.js）。

离线、零第三方依赖。默认跑真实的 canonical 离线基准（P0.6），
聚合出一份 :class:`TelemetrySnapshot` 并写到输出目录：
  - ``telemetry.json`` — 机器可读快照；
  - ``telemetry.js``   — ``window.TELEMETRY = {...}``，供 ``dashboard.html``
    用 ``<script src="telemetry.js">`` 直接引用（file:// 打开即可）。

用法::

    python scripts/emit_telemetry.py                # 真实基准 → ./telemetry/
    python scripts/emit_telemetry.py --demo          # 附带演示大臣/事件数据
    python scripts/emit_telemetry.py --out telemetry --demo
"""

from __future__ import annotations

import argparse
import os
import sys

# 让脚本可直接以仓库根为 CWD 运行
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from huanxin.court.circuit_breaker import CircuitBreaker  # noqa: E402
from huanxin.eval_bench.run import run_suite  # noqa: E402
from huanxin.eval_bench.suites.canonical import build_canonical_suite  # noqa: E402
from huanxin.telemetry import (  # noqa: E402
    MinisterTelemetry,
    collect,
    write_js,
    write_json,
)


def _demo_ministers():
    return [
        MinisterTelemetry(name="math_minister", domain="math", merit=82.5,
                          status="active", success_streak=6),
        MinisterTelemetry(name="code_minister", domain="code", merit=74.0,
                          status="active", success_streak=3),
        MinisterTelemetry(name="retrieval_shadow", domain="retrieval", merit=55.0,
                          status="shadow", success_streak=2),
        MinisterTelemetry(name="weak_minister", domain="general", merit=28.0,
                          status="active", failure_streak=4),
    ]


def _demo_events():
    return [
        {"action": "promote", "minister": "code_minister",
         "merit_before": 68.0, "merit_after": 74.0},
        {"action": "eliminate", "minister": "stale_minister",
         "merit_before": 12.0, "merit_after": 0.0},
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成自进化系统可观测性快照")
    ap.add_argument("--out", default="telemetry", help="输出目录（默认 telemetry/）")
    ap.add_argument("--demo", action="store_true", help="附带演示大臣/事件数据")
    args = ap.parse_args(argv)

    # 真实信号：跑离线 canonical 基准（确定性，无需 LLM key）
    report = run_suite(build_canonical_suite())
    breaker = CircuitBreaker()

    snapshot = collect(
        circuit_breaker=breaker,
        eval_report=report,
        ministers=_demo_ministers() if args.demo else None,
        evolution_events=_demo_events() if args.demo else None,
        cost_total=0.0,
        cost_budget=0.0,
    )

    os.makedirs(args.out, exist_ok=True)
    json_path = write_json(snapshot, os.path.join(args.out, "telemetry.json"))
    js_path = write_js(snapshot, os.path.join(args.out, "telemetry.js"))

    # 复制自包含的静态看板到输出目录（引用同目录 ./telemetry.js）
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    dash_src = os.path.join(repo_root, "dashboard.html")
    dash_dst = os.path.join(args.out, "dashboard.html")
    dash_msg = ""
    if os.path.exists(dash_src):
        import shutil
        shutil.copyfile(dash_src, dash_dst)
        dash_msg = f"\n   {dash_dst}  （用浏览器打开即可查看）"

    print(f"✅ 快照已生成（health={snapshot.health}）：")
    print(f"   {json_path}")
    print(f"   {js_path}{dash_msg}")
    print(f"   eval pass_rate = {report.pass_rate:.1%} ({report.passed}/{report.cases})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
