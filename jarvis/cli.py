"""Emperor CLI — one command to rule the evolutionary court.

Usage:
    emperor init [--path DIR]              Create a new court with default config
    emperor status [--json] [--path DIR]   Show court state summary
    emperor register [--name NAME] ...     Register a minister
    emperor evolve [--cycles N]            Run evolution cycles
    emperor serve [--port PORT]            Start REST API server
    emperor config [show|init]             View or scaffold config
    emperor list                           List all ministers
    emperor history [--limit N]            Show evolution history

Examples:
    emperor init --path ./my_court
    emperor register --domain math --name turing
    emperor evolve --cycles 5
    emperor serve --port 9000
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _load_or_create_court(court_dir: Path):
    """Load genomes from court_dir if they exist, otherwise create fresh."""
    from jarvis.court.court import Court

    court = Court()
    genome_file = court_dir / "genomes.json"
    genome_file.parent.mkdir(parents=True, exist_ok=True)
    court._sm._genome_path = str(genome_file)

    if genome_file.exists():
        try:
            court.load_genomes(str(genome_file))
        except Exception:
            click.echo(
                f"Warning: failed to load genomes from {genome_file}",
                err=True,
            )

    history_file = court_dir / "history.json"
    if history_file.exists():
        try:
            court.load_history(str(history_file))
        except Exception:
            pass

    return court


def _save_court(court, court_dir: Optional[Path] = None):
    """Persist genomes and history if paths configured."""
    court.save_genomes()

    if court_dir:
        history_file = court_dir / "history.json"
        court.save_history(str(history_file))


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

@click.group()
@click.version_option(version="0.1.0", prog_name="emperor")
@click.pass_context
def cli(ctx: click.Context):
    """Emperor — evolutionary AI court management CLI."""
    ctx.ensure_object(dict)


@cli.command()
@click.option(
    "--path", "-p", type=click.Path(), default="./court",
    help="Court directory path",
)
def init(path: str):
    """Initialize a new court directory with default config and founder genome."""
    court_dir = Path(path).resolve()
    court_dir.mkdir(parents=True, exist_ok=True)

    # Seed default config
    config_file = court_dir / "config.yaml"
    if not config_file.exists():
        config_file.write_text(
            "elitism_count: 2\n"
            "crossover_rate: 0.3\n"
            "mutation_rate: 0.05\n"
            "shadow_count: 3\n"
            "cycle_limit: 50\n"
            "task_difficulty: 0.5\n"
            "diversity_weight: 0.3\n"
            "stability_blend: 0.2\n",
            encoding="utf-8",
        )
        click.echo(f"Default config written to {config_file}")

    click.echo(f"Court initialized at {court_dir}")
    click.echo("Next: emperor register --name turing --domain math")


@cli.command()
@click.option(
    "--path", "-p", type=click.Path(), default="./court",
    help="Court directory path",
)
@click.option(
    "--fmt", "output_format", type=click.Choice(["text", "json"]),
    default="text", help="Output format"
)
def status(path: str, output_format: str):
    """Show court state summary."""
    court = _load_or_create_court(Path(path))
    if output_format == "json":
        snap = court.inspect.snapshot()
        click.echo(json.dumps(
            {"total_ministers": snap.total_ministers,
             "active_count": snap.active_count},
            indent=2, default=str,
        ))
    else:
        click.echo(court.summary())


@cli.command()
@click.option(
    "--path", "-p", type=click.Path(), default="./court",
    help="Court directory path",
)
@click.option("--name", "-n", default=None, help="Minister name (auto if omitted)")
@click.option("--domain", "-d", default="general",
              help="Domain: general, math, code, etc.")
@click.option("--temperature", "-t", type=float, default=0.7,
              help="Temperature (0.0-2.0)")
def register(path: str, name: Optional[str], domain: str, temperature: float):
    """Register a new minister into the court."""
    court = _load_or_create_court(Path(path))
    result = court.register(name=name, domain=domain, temperature=temperature)
    click.echo(f"Registered minister: {result}")
    _save_court(court, Path(path))


@cli.command()
@click.option(
    "--path", "-p", type=click.Path(), default="./court",
    help="Court directory path",
)
@click.option("--cycles", "-c", type=int, default=1, help="Number of cycles")
def evolve(path: str, cycles: int):
    """Run evolution cycles on the court."""
    court = _load_or_create_court(Path(path))

    if not court.active_ministers:
        click.echo(
            "No active ministers. Register one first: emperor register"
        )
        raise SystemExit(1)

    if cycles > 100:
        click.echo(
            "Warning: large cycle count; "
            "consider --cycles 10-20 for initial runs"
        )

    result = court.evolve(cycles)
    click.echo(json.dumps(result, indent=2, default=str))
    _save_court(court, Path(path))


@cli.command("list")
@click.option(
    "--path", "-p", type=click.Path(), default="./court",
    help="Court directory path",
)
def list_ministers(path: str):
    """List all ministers in the court."""
    court = _load_or_create_court(Path(path))
    snap = court.inspect.snapshot()
    click.echo(f"Total: {snap.total_ministers}  "
               f"Active: {snap.active_count}")
    click.echo("-" * 66)
    for m in snap.ministers:
        icon = "*" if m.status == "active" else " "
        click.echo(
            f" {icon} {m.name:<16s}  "
            f"merit={m.merit:.3f}  "
            f"temp={m.temperature:.2f}  "
            f"gen={m.generation}"
        )


@cli.command()
@click.option(
    "--path", "-p", type=click.Path(), default="./court",
    help="Court directory path",
)
@click.option("--limit", "-l", type=int, default=10,
              help="Max recent cycles to show")
def history(path: str, limit: int):
    """Show evolution cycle history."""
    court = _load_or_create_court(Path(path))
    total = len(court.history)
    if total == 0:
        click.echo("No evolution history yet. Run: emperor evolve")
        return

    start = max(0, total - limit)
    click.echo(f"Showing cycles {start}–{total - 1} of {total}")
    click.echo("-" * 50)
    for i in range(start, total):
        rec = court.history[i]
        click.echo(
            f"  Cycle {rec.cycle:>3d}:  "
            f"active={rec.active_count}  "
            f"merit_avg={rec.merit_mean:.3f}"
        )


@cli.command()
@click.option(
    "--path", "-p", type=click.Path(), default="./court",
    help="Court directory path",
)
@click.option("--port", type=int, default=8000, help="Server port")
@click.option("--host", default="127.0.0.1", help="Server host")
def serve(path: str, port: int, host: str):
    """Start the Court REST API server."""
    import uvicorn
    from jarvis.court.config import SurvivalConfig
    from jarvis.court_api import create_app

    court_dir = Path(path)
    config_path = court_dir / "config.yaml"

    config = None
    if config_path.exists():
        config = SurvivalConfig.from_yaml(str(config_path))
        click.echo(f"Loaded config from {config_path}")

    app = create_app(config=config)
    click.echo(f"Emperor Court API → http://{host}:{port}")
    click.echo(f"Court directory: {court_dir}")
    uvicorn.run(app, host=host, port=port, log_level="info")


@cli.command()
@click.option(
    "--path", "-p", type=click.Path(), default="./court",
    help="Court directory path",
)
@click.argument("action", type=click.Choice(["show", "init"]))
def config(path: str, action: str):
    """Manage court configuration.

    ACTION is one of: show, init.
    """
    court_dir = Path(path)
    config_file = court_dir / "config.yaml"

    if action == "init":
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            "elitism_count: 2\n"
            "crossover_rate: 0.3\n"
            "mutation_rate: 0.05\n"
            "shadow_count: 3\n"
            "cycle_limit: 50\n"
            "task_difficulty: 0.5\n"
            "diversity_weight: 0.3\n"
            "stability_blend: 0.2\n",
            encoding="utf-8",
        )
        click.echo(f"Config written to {config_file}")
    elif action == "show":
        if config_file.exists():
            click.echo(config_file.read_text(encoding="utf-8"))
        else:
            click.echo(
                f"No config at {config_file}. "
                "Run 'emperor config init' to create one."
            )


# ══════════════════════════════════════════════════════════════════
# Entry points
# ══════════════════════════════════════════════════════════════════

def main():
    cli(prog_name="emperor")


if __name__ == "__main__":
    main()
