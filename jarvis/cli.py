#!/usr/bin/env python3
"""Emperor Core CLI — 多领域 AI Agent 管理系统命令行工具。

Usage:
    jarvis serve                    启动 Dashboard 服务器
    jarvis task <prompt>            提交任务
    jarvis status                   查看系统状态
    jarvis ministers                列出所有大臣
    jarvis evolve                   手动触发进化
    jarvis alerts                   查看活跃告警
    jarvis self-evolve              运行自进化闭环（离线/真实 PR）
    jarvis --version                显示版本号
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap

VERSION = "0.2.0"

# ── 部署侧单一事实来源：容器/云平台统一监听 0.0.0.0:8000 ──────────────
# 优先级：命令行显式参数 > 环境变量 EMPEROR_HOST/EMPEROR_PORT > 下列默认值。
# Dockerfile / docker-compose.yml / render.yaml 三处都以这一组值为准。
DEFAULT_SERVE_HOST = "0.0.0.0"
DEFAULT_SERVE_PORT = 8000

# ANSI colors — only enabled when stdout is a TTY
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BLUE = "\033[94m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _c(text: str, code: str) -> str:
    """Wrap text in ANSI code if stdout is a TTY."""
    if sys.stdout.isatty():
        return f"{code}{text}{_RESET}"
    return text


def _resolve_serve_host(cli_host: str | None) -> str:
    """解析监听地址：命令行 > EMPEROR_HOST > 默认 0.0.0.0。"""
    if cli_host:
        return cli_host
    env_host = os.environ.get("EMPEROR_HOST", "").strip()
    if env_host:
        return env_host
    return DEFAULT_SERVE_HOST


def _resolve_serve_port(cli_port: int | None) -> int:
    """解析监听端口：命令行 > EMPEROR_PORT > 默认 8000。

    环境变量非法（非数字/越界）时回退到默认端口并告警，不让容器直接崩。
    """
    if cli_port:
        return int(cli_port)
    env_port = os.environ.get("EMPEROR_PORT", "").strip()
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            print(f"  [警告] EMPEROR_PORT={env_port!r} 不是合法整数，回退到 {DEFAULT_SERVE_PORT}")
            return DEFAULT_SERVE_PORT
        if 1 <= port <= 65535:
            return port
        print(f"  [警告] EMPEROR_PORT={port} 越界(1-65535)，回退到 {DEFAULT_SERVE_PORT}")
    return DEFAULT_SERVE_PORT


def cmd_serve(args: argparse.Namespace) -> None:
    """启动 Dashboard 服务器（API + 法庭 + 仪表盘）。"""
    from jarvis.emperor import Emperor, EmperorConfig

    host = _resolve_serve_host(getattr(args, "host", None))
    port = _resolve_serve_port(getattr(args, "port", None))

    # EmperorConfig 的 data_dir/court_path 会自行读取 EMPEROR_DATA_DIR /
    # EMPEROR_COURT_PATH，这里只负责把最终 host/port 灌进配置。
    cfg = EmperorConfig()
    cfg.api_host = host
    cfg.api_port = port

    emperor = Emperor(config=cfg)
    emperor.serve(host=host, port=port)


def cmd_task(args: argparse.Namespace) -> None:
    """提交任务。"""
    from jarvis.emperor import Emperor

    emperor = Emperor()
    report = emperor.execute_task(args.prompt, domain=args.domain)

    success = report.get("success", False)
    status_label = _c("成功", _GREEN) if success else _c("失败", _RED)

    print()
    print(f"  任务ID: {report.get('task_id', 'N/A')}")
    print(f"  大臣:   {report.get('minister', 'N/A')}")
    print(f"  状态:   {status_label}")
    print(f"  置信度: {report.get('confidence', 0):.2f}")
    print(f"  耗时:   {report.get('execution_time_ms', 0):.0f}ms")
    print(f"  {'=' * 50}")
    response = report.get("response", "")
    if response:
        print(response)
    else:
        err = report.get("error", "")
        if err:
            print(f"  错误: {err}")
    print(f"  {'=' * 50}")


def cmd_status(args: argparse.Namespace) -> None:
    """查看系统状态。"""
    from jarvis.emperor import Emperor

    emperor = Emperor()
    court = emperor.court
    snap = court.inspect.snapshot()
    ranking = court.merit_ranking

    print()
    print(f"  {_c('Emperor Core', _BOLD)} v{VERSION}")
    print(f"  大臣总数:   {snap.total_ministers}")
    print(f"  活跃大臣:   {snap.active_count}")
    print(f"  进化代数:   {court.cycle}")

    if ranking:
        top = ranking[0]
        avg_merit = sum(r.merit_score for r in ranking) / len(ranking)
        print(f"  平均功绩:   {avg_merit:.1f}")
        print(f"  成功率:     {court.success_rate:.1%}")
        print(f"  榜首:       {top.minister} (merit={top.merit_score:.1f})")

    sched = getattr(emperor, "_scheduler", None)
    if sched is not None and hasattr(sched, "state"):
        from jarvis.court.scheduler import SchedulerState
        state = sched.state
        paused = state == SchedulerState.PAUSED
        running = state == SchedulerState.RUNNING
        state_str = (
            _c("运行中", _GREEN) if running
            else _c("已暂停", _YELLOW) if paused
            else state.name
        )
        print(f"  调度状态:   {state_str}")

    domains: set[str] = set()
    for m in snap.ministers:
        domains.add(m.domain)
    print(f"  活跃领域:   {len(domains)} ({', '.join(sorted(domains))})")
    print()


def cmd_ministers(args: argparse.Namespace) -> None:
    """列出所有大臣。"""
    from jarvis.emperor import Emperor

    emperor = Emperor()
    court = emperor.court
    snap = court.inspect.snapshot()

    if not snap.ministers:
        print("\n  暂无大臣\n")
        return

    genomes = court._sm._genomes
    ranking_map = {r.minister: r for r in court.merit_ranking}

    merged = []
    for m in snap.ministers:
        genome = genomes.get(m.name)
        merit_report = ranking_map.get(m.name)
        merit = float(merit_report.merit_score) if merit_report else float(m.merit)
        streak = getattr(genome, "success_streak", 0) if genome else 0
        fail_streak = getattr(genome, "failure_streak", 0) if genome else 0
        total_tasks = getattr(genome, "total_tasks", 0) if genome else 0
        capability_hits = getattr(genome, "capability_hits", 0) if genome else 0

        if streak >= 3:
            status = f"{_c(f'{streak}连胜', _GREEN)}"
        elif fail_streak >= 3:
            status = f"{_c(f'{fail_streak}连败', _RED)}"
        else:
            status = "--"

        merged.append({
            "name": m.name,
            "domain": m.domain,
            "merit": merit,
            "status": status,
            "streak": streak,
            "fail_streak": fail_streak,
            "total_tasks": total_tasks,
            "capability_hits": capability_hits,
        })

    merged.sort(key=lambda x: x["merit"], reverse=True)

    print()
    header = f"  {'排名':<4} {'名称':<16} {'领域':<12} {'功绩':<16} {'任务':<8} {'状态'}"
    print(header)
    print(f"  {'-' * 4} {'-' * 16} {'-' * 12} {'-' * 16} {'-' * 8} {'-' * 12}")

    for i, m in enumerate(merged, 1):
        bar_len = min(int(m["merit"] / 5), 20)
        bar = _c("█" * bar_len + "░" * (20 - bar_len), _BLUE)
        merit_str = f"{bar} {m['merit']:.0f}"

        tasks_str = f"{m['total_tasks']}"
        if m["total_tasks"] > 0:
            hit_rate = m["capability_hits"] * 100 // m["total_tasks"]
            tasks_str += f"({hit_rate}%)"

        print(f"  {i:<4} {m['name']:<16} {m['domain']:<12} {merit_str:<28} {tasks_str:<8} {m['status']}")
    print()


def cmd_evolve(args: argparse.Namespace) -> None:
    """手动触发进化。"""
    from jarvis.emperor import Emperor

    emperor = Emperor()
    court = emperor.court

    if not court.active_ministers:
        print(f"\n  {_c('无活跃大臣，请先注册大臣', _YELLOW)}\n")
        return

    print(f"\n  {_c('正在执行进化...', _BOLD)}")
    try:
        result = court.evolve(args.cycles)
        if isinstance(result, dict):
            active = result.get("active_count", "?")
            eliminated = result.get("eliminated_count", "?")
            spawned = result.get("new_spawns", "?")
            print(f"  {_c('进化完成', _GREEN)}: active={active}, eliminated={eliminated}, spawned={spawned}")
        else:
            print(f"  {_c('进化完成', _GREEN)}")
    except Exception as e:
        print(f"  {_c(f'进化失败: {e}', _RED)}")
    print()


def cmd_alerts(args: argparse.Namespace) -> None:
    """查看活跃告警。"""
    from jarvis.emperor import Emperor

    emperor = Emperor()
    alert_manager = emperor.alerts

    if alert_manager is None:
        print("\n  告警管理器未初始化\n")
        return

    history = alert_manager.history(limit=20)
    if not history:
        print(f"\n  {_c('无活跃告警', _GREEN)}\n")
        return

    level_map = {
        "critical": _c("CRIT", _RED),
        "warning": _c("WARN", _YELLOW),
        "info": _c("INFO", _BLUE),
    }

    print(f"\n  {'级别':<10} {'规则':<30} {'消息'}")
    print(f"  {'-' * 10} {'-' * 30} {'-' * 40}")
    for a in history:
        level_str = level_map.get(a.severity, a.severity.upper())
        rule = a.rule_name[:30]
        msg = (a.message or "")[:60]
        print(f"  {level_str:<16} {rule:<30} {msg}")
    print()


def cmd_self_evolve(args: argparse.Namespace) -> None:
    """运行自进化闭环（离线确定性 / 真实 PR）。"""
    import os
    import sys

    # scripts/ 不是包，按需把它加入路径再导入编排驱动
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    from run_self_evolve import run_orchestrator, load_config, SelfEvolveConfig

    cfg = load_config(getattr(args, "config", None)) if getattr(args, "config", None) \
        else SelfEvolveConfig()
    if getattr(args, "cycles", 0):
        cfg.cycles = args.cycles
    if getattr(args, "seed", None) is not None:
        cfg.seed = args.seed
    if getattr(args, "repo", None):
        cfg.repo = args.repo
    if getattr(args, "resume", False):
        cfg.resume = True
    if getattr(args, "approval", False):
        cfg.use_approval_engine = True
    if getattr(args, "auto_approve", False):
        cfg.auto_approve = True
        cfg.use_approval_engine = True
    if getattr(args, "audit", False):
        cfg.use_audit = True
    if getattr(args, "no_safety_gate", False):
        cfg.use_safety_gate = False
    if getattr(args, "no_snapshots", False):
        cfg.enable_snapshots = False
    if getattr(args, "resource_seconds", None) is not None:
        cfg.resource_seconds = args.resource_seconds
    if getattr(args, "resource_max_ops", None) is not None:
        cfg.resource_max_ops = args.resource_max_ops
    if getattr(args, "golden_pass_rate_min", None) is not None:
        cfg.golden_pass_rate_min = args.golden_pass_rate_min
    if getattr(args, "executor", None):
        cfg.executor = args.executor
    if getattr(args, "self_learn", False):
        cfg.self_learn = True
    if getattr(args, "no_self_learn", False):
        cfg.self_learn = False
    if getattr(args, "no_memory", False):
        cfg.use_memory = False
    if getattr(args, "memory_path", None):
        cfg.memory_path = args.memory_path
    if getattr(args, "warm_start_memory", False):
        cfg.warm_start_from_memory = True
    if getattr(args, "memory_recency_decay", None) is not None:
        cfg.memory_recency_decay = float(args.memory_recency_decay)
    if getattr(args, "memory_max_per_group", None) is not None:
        cfg.memory_max_per_group = int(args.memory_max_per_group)
    if getattr(args, "task_file", None):
        cfg.task_file = args.task_file
    if getattr(args, "task", None):  # 内联真实任务（可重复）
        cfg.tasks = [
            {"id": f"cli-{i:02d}", "prompt": p, "domain": "general"}
            for i, p in enumerate(args.task)
        ]

    se_cmd = getattr(args, "se_command", None)
    if se_cmd == "safety-check":
        from run_self_evolve import run_safety_check
        core = cfg.core_ministers if cfg.core_ministers else ("math_alpha", "reason_gamma")
        rc = run_safety_check(
            path=getattr(args, "path", None) or cfg.genome_state_path,
            core_ministers=core,
            quality_floor=cfg.quality_floor,
            max_regression=cfg.max_regression,
        )
        sys.exit(rc)
    if se_cmd == "rollback":
        from run_self_evolve import run_rollback
        rc = run_rollback(
            snapshot_dir=cfg.snapshot_dir,
            snapshot_id=getattr(args, "to", "") or "",
            list_only=bool(getattr(args, "list", False)),
            genome_state_path=cfg.genome_state_path,
        )
        sys.exit(rc)

    out = getattr(args, "out", None) or "telemetry"
    try:
        rc = run_orchestrator(
            cfg, out_dir=out,
            live=bool(getattr(args, "live", False)),
            no_writeback=bool(getattr(args, "no_writeback", False)),
        )
        sys.exit(rc)
    except Exception as e:  # pragma: no cover - 入口级兜底
        print(f"\n  {_c(f'自进化运行失败: {e}', _RED)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Emperor Core — 多领域 AI Agent 管理系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              jarvis serve                       启动 Dashboard
              jarvis serve --port 8080           指定端口
              jarvis task "计算 2+3"             提交任务
              jarvis task --domain math "计算 pi" 指定领域
              jarvis status                      查看状态
              jarvis ministers                   大臣列表
              jarvis evolve                      手动进化
              jarvis alerts                      告警列表
              jarvis self-evolve                运行自进化闭环（离线确定/真实 PR）
        """),
    )

    parser.add_argument(
        "--version", action="version", version=f"jarvis {VERSION}"
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # ── serve ──
    serve_parser = subparsers.add_parser("serve", help="启动 Dashboard 服务器")
    serve_parser.add_argument(
        "--host", default=None,
        help=f"监听地址 (未指定时读 EMPEROR_HOST，默认: {DEFAULT_SERVE_HOST})",
    )
    serve_parser.add_argument(
        "--port", type=int, default=0,
        help=f"监听端口 (未指定时读 EMPEROR_PORT，默认: {DEFAULT_SERVE_PORT})",
    )
    serve_parser.set_defaults(func=cmd_serve)

    # ── task ──
    task_parser = subparsers.add_parser("task", help="提交任务")
    task_parser.add_argument("prompt", help="任务描述")
    task_parser.add_argument(
        "--domain", "-d", default="general", help="任务领域 (默认: general)"
    )
    task_parser.set_defaults(func=cmd_task)

    # ── status ──
    status_parser = subparsers.add_parser("status", help="系统状态")
    status_parser.set_defaults(func=cmd_status)

    # ── ministers ──
    ministers_parser = subparsers.add_parser("ministers", help="大臣列表")
    ministers_parser.set_defaults(func=cmd_ministers)

    # ── evolve ──
    evolve_parser = subparsers.add_parser("evolve", help="手动进化")
    evolve_parser.add_argument(
        "--cycles", "-c", type=int, default=1, help="进化轮数 (默认: 1)"
    )
    evolve_parser.set_defaults(func=cmd_evolve)

    # ── alerts ──
    alerts_parser = subparsers.add_parser("alerts", help="告警列表")
    alerts_parser.set_defaults(func=cmd_alerts)

    # ── self-evolve ──
    se_parser = subparsers.add_parser(
        "self-evolve", help="运行自进化闭环（离线确定/真实 PR）"
    )
    se_parser.add_argument("--config", default=None, help="YAML 配置文件")
    se_parser.add_argument("--cycles", "-c", type=int, default=0, help="进化轮数")
    se_parser.add_argument("--seed", type=int, default=None, help="确定性种子")
    se_parser.add_argument("--out", default="telemetry", help="输出目录")
    se_parser.add_argument("--repo", default=None, help="写回目标仓库")
    se_parser.add_argument("--live", action="store_true", help="真实写回（需 gh 凭据）")
    se_parser.add_argument("--no-writeback", action="store_true", help="禁用写回")
    se_parser.add_argument("--resume", action="store_true", help="从基因检查点续跑")
    se_parser.add_argument("--approval", action="store_true", help="接入人类审批门")
    se_parser.add_argument("--auto-approve", action="store_true",
                           help="审批门自动批准（仅离线/CI）")
    se_parser.add_argument("--audit", action="store_true", help="写入不可篡改审计库")
    se_parser.add_argument("--no-safety-gate", action="store_true", help="关闭金标准安全闸")
    se_parser.add_argument("--no-snapshots", action="store_true", help="关闭每轮安全快照")
    se_parser.add_argument("--resource-seconds", type=float, default=None,
                           help="单轮墙钟预算（秒，越限即熔断）")
    se_parser.add_argument("--resource-max-ops", type=int, default=None,
                           help="单轮操作数上限（如 LLM 调用次数）")
    se_parser.add_argument("--golden-pass-rate-min", type=float, default=None,
                           help="行为级金标准地板：最优大臣基准答对率下限（防 reward-hacking）")
    se_parser.add_argument("--executor", choices=["sim", "real", "auto"], default=None,
                           help="执行器：real=真实离线求解/真实LLM（默认） sim=基因模拟")
    se_parser.add_argument("--self-learn", action="store_true",
                           help="开启自我学习：真实成败即时微调基因（向最优区靠拢）")
    se_parser.add_argument("--no-self-learn", action="store_true", help="关闭自我学习")
    se_parser.add_argument("--task-file", default=None,
                           help="自定义真实任务文件（YAML/JSON: [{id,prompt,domain,expected}]）")
    se_parser.add_argument("--task", action="append", default=None,
                           help="内联真实任务 prompt（可重复），系统真实执行并从中学习")
    se_parser.add_argument("--no-memory", action="store_true",
                           help="关闭经验记忆（默认开：真实成败落盘，跨重启累积学习）")
    se_parser.add_argument("--memory-path", default=None,
                           help="经验记忆持久化路径（默认 jarvis/court/memory.json）")
    se_parser.add_argument("--warm-start-memory", action="store_true",
                           help="启动时用已落盘经验轻推冷启动基因（opt-in；不覆盖已恢复的基因检查点）")
    se_parser.add_argument("--memory-recency-decay", default=None, type=float,
                           help="历史成功率的时间衰减系数（0<d<1=新鲜样本权重更高，1.0=等权，默认 1.0）")
    se_parser.add_argument("--memory-max-per-group", default=None, type=int,
                           help="每(大臣,领域)留存上限，超限丢弃最旧样本（默认不封顶）")
    # ── 嵌套子命令：安全校验 / 回滚 ──
    se_sub = se_parser.add_subparsers(dest="se_command")
    se_sc = se_sub.add_parser("safety-check", help="对当前基因快照跑金标准安全闸")
    se_sc.add_argument("--path", default=None, help="基因快照路径（默认 genome_state.json）")
    se_rb = se_sub.add_parser("rollback", help="基因快照回滚（撤销自修改）")
    se_rb.add_argument("--list", action="store_true", help="列出全部快照")
    se_rb.add_argument("--to", default="", help="回滚到指定 snapshot_id")
    se_parser.set_defaults(func=cmd_self_evolve)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
