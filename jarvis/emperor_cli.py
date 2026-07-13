"""
Emperor CLI (天子殿) — Imperial Court interactive interface.

Usage:
    emperor court      Start interactive court session
    emperor status     Court status dashboard
    emperor history    Evolution history log
    emperor serve      Start API server

The Emperor CLI presents the Imperial Court system through a polished
terminal interface using Rich. Ministers deliberate in real-time,
the MeritBoard ranks publicly, and evolution events display with
color-coded significance.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Add project root to path when running as module
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.box import Box, HEAVY_EDGE, ROUNDED, SIMPLE
from rich.align import Align
from rich.columns import Columns
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.spinner import Spinner
from rich.style import Style
from rich.syntax import Syntax
from rich import box

from jarvis.court.emperor import Emperor, ImperialCourt
from jarvis.court.evolution import (
    EvolutionAction,
    EvolutionEvent,
    MinisterStatus,
)

# ---------------------------------------------------------------------------
# Styling constants
# ---------------------------------------------------------------------------

console = Console()

STYLE_BORDER = Style(color="bright_yellow")
STYLE_TITLE = Style(color="gold1", bold=True)
STYLE_SUCCESS = Style(color="green")
STYLE_FAILURE = Style(color="red")
STYLE_WARNING = Style(color="orange1")
STYLE_INFO = Style(color="cyan")
STYLE_HIGHLIGHT = Style(color="bright_magenta", bold=True)
STYLE_DIM = Style(color="grey66")
STYLE_MERIT_HIGH = Style(color="green", bold=True)
STYLE_MERIT_MID = Style(color="yellow")
STYLE_MERIT_LOW = Style(color="red")
STYLE_ELIMINATED = Style(color="grey37", strike=True)

RANK_ICONS = {
    "GRANDEE": "👑",
    "MINISTER": "🏅",
    "OFFICER": "⭐",
    "KNIGHT": "🔰",
    "COMMONER": "🌱",
}

STATUS_ICONS = {
    "ACTIVE": "◉",
    "SHADOW": "◎",
    "PROBATION": "⚠",
    "ELIMINATED": "✗",
}

STATUS_STYLES = {
    "ACTIVE": Style(color="green"),
    "SHADOW": Style(color="yellow"),
    "PROBATION": Style(color="orange1", bold=True),
    "ELIMINATED": Style(color="grey37", strike=True),
}

ACTION_STYLES = {
    EvolutionAction.PROMOTE: Style(color="green"),
    EvolutionAction.DEMOTE: Style(color="yellow"),
    EvolutionAction.PROBATION_MARK: Style(color="orange1"),
    EvolutionAction.ELIMINATE: Style(color="red", bold=True),
    EvolutionAction.CLONE_MUTATE: Style(color="bright_magenta"),
    EvolutionAction.SPAWN_SPECIALIST: Style(color="cyan"),
    EvolutionAction.TUNE_PARAMS: Style(color="blue"),
    EvolutionAction.NO_ACTION: Style(color="grey66"),
}

ACTION_LABELS = {
    EvolutionAction.PROMOTE: "升格",
    EvolutionAction.DEMOTE: "降格",
    EvolutionAction.PROBATION_MARK: "考核",
    EvolutionAction.ELIMINATE: "淘汰",
    EvolutionAction.CLONE_MUTATE: "克隆",
    EvolutionAction.SPAWN_SPECIALIST: "招募",
    EvolutionAction.TUNE_PARAMS: "调参",
    EvolutionAction.NO_ACTION: "无",
}


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

EMPEROR_BANNER = r"""
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║     ███████╗███╗   ███╗██████╗ ███████╗██████╗  ██████╗ ██████╗  ║
║     ██╔════╝████╗ ████║██╔══██╗██╔════╝██╔══██╗██╔═══██╗██╔══██╗ ║
║     █████╗  ██╔████╔██║██████╔╝█████╗  ██████╔╝██║   ██║██████╔╝ ║
║     ██╔══╝  ██║╚██╔╝██║██╔═══╝ ██╔══╝  ██╔══██╗██║   ██║██╔══██╗ ║
║     ███████╗██║ ╚═╝ ██║██║     ███████╗██║  ██║╚██████╔╝██║  ██║ ║
║     ╚══════╝╚═╝     ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝ ║
║                                                                  ║
║     帝 国 宫 廷  ·  Imperial Court AI Orchestrator               ║
║     八大臣自治 · 功勋榜排行 · 自进化淘汰 · 三省合议              ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""


def print_banner() -> None:
    """Display the Emperor banner."""
    console.print(EMPEROR_BANNER, style=STYLE_TITLE)


# ---------------------------------------------------------------------------
# Emperor singleton — initialized once, shared across commands
# ---------------------------------------------------------------------------

_emperor_singleton: Optional[Emperor] = None


def get_emperor() -> Emperor:
    """Get or create the Emperor singleton."""
    global _emperor_singleton
    if _emperor_singleton is None:
        _emperor_singleton = Emperor()
    return _emperor_singleton


# ---------------------------------------------------------------------------
# Dashboard rendering
# ---------------------------------------------------------------------------

def render_court_status(emperor: Emperor) -> Table:
    """Build a court status dashboard table."""
    court = emperor.court
    metrics = court.get_court_metrics()

    table = Table(
        title="🏛  帝国宫廷 · 朝堂现状",
        title_style=STYLE_TITLE,
        box=HEAVY_EDGE,
        border_style=STYLE_BORDER,
        show_header=True,
        header_style=Style(bold=True, color="gold1"),
    )

    table.add_column("大臣", style="bold")
    table.add_column("爵位", justify="center")
    table.add_column("功勋", justify="right")
    table.add_column("胜率", justify="right")
    table.add_column("温度", justify="right")
    table.add_column("状态", justify="center")

    # Merit ranking
    ranking = court.merit_board.get_ranking()

    for report in ranking:
        minister_name = report.minister
        rank_icon = RANK_ICONS.get(report.rank.name, "")
        merit_style = (
            STYLE_MERIT_HIGH if report.merit_score >= 60
            else STYLE_MERIT_MID if report.merit_score >= 30
            else STYLE_MERIT_LOW
        )

        # Get minister state
        minister = court.ministers.get(minister_name)
        if minister:
            metrics_entry = minister.get_evolution_metrics()
            temp = f"{metrics_entry['current_temperature']:.2f}"
            state_str = minister.state.name
        else:
            temp = "—"
            state_str = "UNKNOWN"

        # Evolution status
        evo_status = court.survival.get_status(minister_name)
        status_icon = STATUS_ICONS.get(evo_status.name, "?")
        status_style = STATUS_STYLES.get(evo_status.name, Style())

        row_style = STYLE_ELIMINATED if report.eliminated else None

        table.add_row(
            f"{rank_icon} {minister_name}",
            f"[{merit_style}]{report.rank.name}[/]",
            f"[{merit_style}]{report.merit_score:.1f}[/]",
            f"{report.success_rate:.1%}",
            temp,
            f"[{status_style}]{status_icon} {evo_status.name}[/]",
            style=row_style,
        )

    # Footer
    table.caption = (
        f"法令数: {metrics['decree_count']}  |  "
        f"近20条胜率: {metrics['recent_success_rate']:.1%}  |  "
        f"最优大臣: {metrics.get('top_performer', '无')}  |  "
        f"下次进化: {metrics['decrees_until_next_evolution']} 条后"
    )

    return table


def render_evolution_history(emperor: Emperor) -> Panel:
    """Build an evolution history panel."""
    events = emperor.court.survival.get_evolution_history()
    if not events:
        return Panel(
            "暂无进化记录。递交奏章后，系统将自动进化。",
            title="📜 进化史",
            title_align="left",
            border_style=STYLE_BORDER,
        )

    lines: list[str] = []
    for i, event in enumerate(events[-30:], 1):
        action_style = ACTION_STYLES.get(event.action, Style())
        action_label = ACTION_LABELS.get(event.action, "?")
        time_str = event.timestamp[:19].replace("T", " ")
        reason = event.reason

        # Build line
        line = (
            f"[{STYLE_DIM}]{i:>2}.[/] "
            f"[{action_style}]{action_label}[/] "
            f"[bold]{event.minister}[/bold] "
            f"[{STYLE_DIM}]{time_str}[/]\n"
            f"     {reason}"
        )
        lines.append(line)

    content = "\n\n".join(lines)
    return Panel(
        content,
        title=f"📜 进化史 · 共 {len(events)} 条目",
        title_align="left",
        border_style=STYLE_BORDER,
    )


def render_merit_leaderboard(emperor: Emperor) -> Table:
    """Build a compact merit leaderboard."""
    board = emperor.court.merit_board.get_leaderboard()

    table = Table(
        title="🏆 功勋榜",
        title_style=STYLE_TITLE,
        box=SIMPLE,
        border_style=STYLE_BORDER,
        show_header=True,
        header_style=Style(bold=True),
    )

    table.add_column("#", justify="right", style=STYLE_DIM)
    table.add_column("大臣")
    table.add_column("功勋", justify="right")
    table.add_column("趋势", justify="center")
    table.add_column("连胜", justify="right")

    for entry in board["rankings"]:
        trend_icon = "↗" if entry["trend"] == "rising" else "↘" if entry["trend"] == "falling" else "→"
        trend_style = (
            Style(color="green") if entry["trend"] == "rising"
            else Style(color="red") if entry["trend"] == "falling"
            else Style(color="grey66")
        )
        streak = entry["streak"]
        streak_str = f"{streak:+d}" if streak != 0 else "—"

        table.add_row(
            str(entry["position"]),
            entry["minister"],
            f"{entry['merit_score']:.1f}",
            f"[{trend_style}]{trend_icon}[/]",
            streak_str,
        )

    return table


def render_minister_detail(emperor: Emperor, minister_name: str) -> Optional[Panel]:
    """Render a detailed view of a single minister."""
    court = emperor.court
    minister = court.ministers.get(minister_name)
    if minister is None:
        return None

    metrics = minister.get_evolution_metrics()
    genome = court.survival.get_genome(minister_name)
    rating = court.merit_board.get_ranking()
    merit_report = next((r for r in rating if r.minister == minister_name), None)

    lines = [
        f"[bold]{minister.profile.title}[/bold] — {minister.profile.archetype}",
        f"领域: {minister.profile.domain}",
        f"决策风格: {minister.profile.decision_style}",
        "",
        "[bold]能力画像[/bold]",
        f"  ✅ {', '.join(minister.profile.strengths[:4])}",
        f"  ❌ {', '.join(minister.profile.weaknesses[:3])}",
        "",
    ]

    if merit_report:
        lines.extend([
            f"[bold]功勋[/bold]: {merit_report.merit_score:.1f} ({merit_report.rank.name})",
            f"  总派发: {merit_report.total_dispatches}  |  胜率: {merit_report.success_rate:.1%}",
            f"  平均置信度: {merit_report.avg_confidence:.3f}",
            f"  连续: {merit_report.streak:+d}  |  趋势: {merit_report.recent_trend}",
        ])

    if genome:
        lines.extend([
            "",
            "[bold]基因组[/bold]",
            f"  世代: {genome.generation}  |  父代: {genome.parent or '原始'}",
            f"  温度: {genome.temperature:.3f}",
            f"  置信基线: {genome.confidence_baseline:.3f}",
            f"  探索率: {genome.exploration_rate:.3f}",
        ])

    return Panel(
        "\n".join(lines),
        title=f"👤 {minister_name}",
        title_align="left",
        border_style=STYLE_BORDER,
    )


# ---------------------------------------------------------------------------
# Interactive court session
# ---------------------------------------------------------------------------

async def interactive_court(emperor: Emperor) -> None:
    """Run the interactive court session loop."""
    print_banner()
    console.print()
    console.print(render_court_status(emperor))
    console.print()

    console.print(
        "[bold]上奏规则:[/bold] 直接输入任务内容，或输入 [cyan]/help[/] 查看命令",
        style=STYLE_DIM,
    )
    console.print()

    while True:
        try:
            user_input = console.input("[bold gold1]奏章 > [/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold]退朝。[/]")
            break

        if not user_input:
            continue

        # Commands
        if user_input.startswith("/"):
            cmd = user_input[1:].strip().lower()
            if cmd in ("quit", "exit", "退朝"):
                console.print("[bold]退朝。[/]")
                break
            elif cmd in ("status", "状态"):
                console.print()
                console.print(render_court_status(emperor))
                console.print()
            elif cmd in ("history", "进化"):
                console.print()
                console.print(render_evolution_history(emperor))
                console.print()
            elif cmd in ("ranking", "功勋榜", "rank"):
                console.print()
                console.print(render_merit_leaderboard(emperor))
                console.print()
            elif cmd in ("help", "帮助"):
                _print_help()
            elif cmd.startswith("info ") or cmd.startswith("详情 "):
                name = cmd.split(maxsplit=1)[1] if " " in cmd else ""
                panel = render_minister_detail(emperor, name)
                if panel:
                    console.print()
                    console.print(panel)
                    console.print()
                else:
                    console.print(f"[{STYLE_WARNING}]未找到大臣: {name}[/]")
            else:
                console.print(f"[{STYLE_DIM}]未知命令: {cmd}。输入 /help 查看帮助[/]")
            continue

        # Submit petition
        console.print()
        with Progress(
            SpinnerColumn(spinner_name="dots"),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task(
                f"[cyan]朝堂议事中… 「{user_input[:40]}…」[/]", total=None
            )
            start_time = time.monotonic()

            try:
                decree = await emperor.receive_petition(user_input)
            except Exception as e:
                progress.stop()
                console.print(f"[{STYLE_FAILURE}]朝议失败: {e}[/]")
                continue

            elapsed = (time.monotonic() - start_time) * 1000
            progress.update(task, completed=True)

        # Display decree
        _display_decree(decree, elapsed)
        console.print()


def _display_decree(decree, elapsed_ms: float) -> None:
    """Render a decree result card."""
    success_style = STYLE_SUCCESS if decree.success else STYLE_FAILURE
    status_text = "✓ 准奏" if decree.success else "✗ 驳回"

    # Header
    console.print(
        Panel.fit(
            f"[bold]{status_text}[/] · {decree.decree_id} "
            f"[{STYLE_DIM}]耗时 {elapsed_ms:.0f}ms · 置信度 {decree.confidence:.1%}[/]",
            border_style=success_style,
            box=ROUNDED,
        )
    )

    # Output
    if decree.output:
        console.print(Panel(decree.output, title="[bold]谕旨[/]", border_style=STYLE_BORDER))

    # Ministers consulted
    if decree.ministers_consulted:
        minister_list = " · ".join(decree.ministers_consulted)
        console.print(f"[{STYLE_DIM}]议事大臣: {minister_list}[/]")

    # Dissenting opinions
    if decree.dissenting_opinions:
        console.print(f"[{STYLE_WARNING}]异议: {'; '.join(decree.dissenting_opinions)}[/]")

    # Court session indicator
    if decree.court_session:
        console.print(f"[{STYLE_INFO}]⚖ 朝堂合议 (三省合议)[/]")

    console.print()


def _print_help() -> None:
    """Print help text."""
    help_table = Table(title="[bold]可用命令[/]", box=SIMPLE, border_style=STYLE_BORDER)
    help_table.add_column("命令", style="cyan")
    help_table.add_column("说明")

    help_table.add_row("/status 或 /状态", "显示朝堂现状仪表盘")
    help_table.add_row("/ranking 或 /功勋榜", "显示功勋排行")
    help_table.add_row("/history 或 /进化", "显示进化历史记录")
    help_table.add_row("/info <大臣名> 或 /详情", "查看某大臣详细信息")
    help_table.add_row("/quit 或 /退朝", "退出朝堂")
    help_table.add_row("[直接输入任务]", "向天子递交奏章，启动大臣议事")

    console.print()
    console.print(help_table)
    console.print()


# ---------------------------------------------------------------------------
# Status dashboard (snapshot)
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> None:
    """Display full court status dashboard as a snapshot."""
    emperor = get_emperor()

    print_banner()
    console.print()
    console.print(render_court_status(emperor))
    console.print()
    console.print(render_merit_leaderboard(emperor))
    console.print()

    # Evolution summary
    evolution = emperor.court.survival
    events = evolution.get_evolution_history()
    if events:
        last_event = events[-1]
        console.print(
            f"[{STYLE_DIM}]最近进化: {last_event.timestamp[:19]} — "
            f"{ACTION_LABELS.get(last_event.action)} {last_event.minister}[/]"
        )

    # Counts
    active = evolution.get_active_ministers()
    shadow = evolution.get_shadow_ministers()
    eliminated = evolution.get_eliminated_ministers()

    console.print(
        f"[{STYLE_DIM}]活跃: {len(active)} | 影阁: {len(shadow)} | 淘汰: {len(eliminated)}[/]"
    )
    console.print()


# ---------------------------------------------------------------------------
# Evolution history
# ---------------------------------------------------------------------------

def cmd_history(args: argparse.Namespace) -> None:
    """Display full evolution history."""
    emperor = get_emperor()

    print_banner()
    console.print()
    console.print(render_evolution_history(emperor))
    console.print()


# ---------------------------------------------------------------------------
# API server
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> None:
    """Start the Emperor API server."""
    print_banner()
    console.print()
    console.print(f"[bold]启动 Emperor API 服务[/] — {args.host}:{args.port}")
    console.print()

    async def _serve() -> None:
        # Start emperor
        emperor = get_emperor()
        console.print(
            f"[{STYLE_SUCCESS}]宫廷就绪: {len(emperor.court.ministers)} 位大臣[/]"
        )

        # Start uvicorn
        import uvicorn
        config = uvicorn.Config(
            app="jarvis.api.server:app",
            host=args.host,
            port=args.port,
            log_level="info",
            reload=args.reload,
        )
        server = uvicorn.Server(config)
        await server.serve()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        console.print("\n[bold]服务已停止。[/]")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Main entry point for emperor CLI."""
    parser = argparse.ArgumentParser(
        prog="emperor",
        description="Emperor — Imperial Court AI Orchestrator",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # emperor court — interactive mode (default)
    court_parser = subparsers.add_parser("court", help="Start interactive court session")
    court_parser.set_defaults(func=cmd_court)

    # emperor status
    status_parser = subparsers.add_parser("status", help="Display court status dashboard")
    status_parser.set_defaults(func=cmd_status)

    # emperor history
    history_parser = subparsers.add_parser("history", help="Display evolution history")
    history_parser.set_defaults(func=cmd_history)

    # emperor serve
    serve_parser = subparsers.add_parser("serve", help="Start API server")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    serve_parser.add_argument("--port", type=int, default=8000, help="Bind port")
    serve_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    serve_parser.set_defaults(func=cmd_serve)

    args = parser.parse_args()

    if not args.command:
        # Default to interactive court mode
        cmd_court(args)
        return

    args.func(args)


def cmd_court(args: argparse.Namespace) -> None:
    """Start interactive court session."""
    emperor = get_emperor()
    try:
        asyncio.run(interactive_court(emperor))
    except KeyboardInterrupt:
        console.print("\n[bold]退朝。[/]")


if __name__ == "__main__":
    main()
