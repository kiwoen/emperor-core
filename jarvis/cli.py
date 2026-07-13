"""
JARVIS CLI — Command-line entry point with subcommands.

Usage:
    jarvis serve       Start the API server
    jarvis chat        Interactive chat mode
    jarvis status      Display system status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path when running as module
sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis.core.orchestrator import Orchestrator
from jarvis.core.config import JARVISConfig, load_config
from jarvis.memory.engine import MemoryEngine
from jarvis.evolution.controller import EvolutionController
from jarvis.sandbox import SandboxManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("jarvis.cli")


def _build_orchestrator(config: JARVISConfig) -> Orchestrator:
    """Initialize all subsystems and return a ready Orchestrator."""
    from jarvis.core.llm import init_llm

    init_llm(config)

    memory = MemoryEngine(
        persist_dir=str(Path(config.data_dir) / "memory"),
        compression_threshold=getattr(config.memory, "auto_compress_threshold", 5000),
    )

    sandbox = SandboxManager(
        engine=getattr(config.sandbox, "engine", "direct"),
        memory_limit=getattr(config.sandbox, "memory_limit", 512),
        cpu_limit=getattr(config.sandbox, "cpu_limit", 1.0),
        timeout_seconds=getattr(config.sandbox, "timeout_seconds", 30),
        network_enabled=getattr(config.sandbox, "network_enabled", False),
    )

    evolution = EvolutionController(data_dir=Path(config.data_dir), config=config)

    orchestrator = Orchestrator(
        memory_engine=memory,
        evolution_controller=evolution,
        sandbox_manager=sandbox,
    )

    orchestrator.load_all_domains()
    loaded = orchestrator.registry.list_domains()
    logger.info("Loaded %d domains: %s", len(loaded), [d.name for d in loaded])

    return orchestrator


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the JARVIS API server."""
    config = load_config()

    orchestrator = _build_orchestrator(config)

    async def _serve() -> None:
        from jarvis.api.server import start_server

        # Patch uvicorn config with CLI args
        import jarvis.api.server as api_server

        api_server._start_time = __import__("time").time()

        import uvicorn

        uvicorn_config = uvicorn.Config(
            app="jarvis.api.server:app",
            host=args.host,
            port=args.port,
            log_level="info",
            reload=args.reload,
        )
        server = uvicorn.Server(uvicorn_config)

        # Set global refs so API endpoints work
        api_server.orchestrator_ref = orchestrator
        api_server.config_ref = config

        await server.serve()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        print("\nServer stopped.")


def cmd_chat(args: argparse.Namespace) -> None:
    """Run JARVIS in interactive chat mode."""
    config = load_config()
    orchestrator = _build_orchestrator(config)

    banner = f"""
╔══════════════════════════════════════════════════════════════╗
║     JARVIS v{config.version:<47}║
║     "At your service, sir."                                  ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)
    print("Type 'exit' or 'quit' to stop.\n")

    async def _chat() -> None:
        while True:
            try:
                user_input = input("You > ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nShutting down...")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "bye"):
                print("Goodbye, sir.")
                break

            result = await orchestrator.execute(user_input)
            if result.success:
                print(f"JARVIS > {result.output}")
            else:
                print(f"JARVIS > [ERROR] {result.error}")

    try:
        asyncio.run(_chat())
    except KeyboardInterrupt:
        print("\nGoodbye.")


def cmd_status(args: argparse.Namespace) -> None:
    """Display JARVIS system status."""
    config = load_config()
    orchestrator = _build_orchestrator(config)

    async def _status() -> None:
        import time as _time

        domains = orchestrator.registry.list_domains()
        mem_stats = await orchestrator.memory.get_stats()
        evo = orchestrator.evolution

        print(f"JARVIS Core v{config.version}")
        print(f"  Domains loaded : {len(domains)} ({', '.join(d.name for d in domains)})")
        print(f"  Memory entries : {mem_stats['episodic_count']} episodic + {mem_stats['semantic_count']} semantic")
        print(f"  ChromaDB       : {'enabled' if mem_stats['chromadb_enabled'] else 'disabled'}")
        print(f"  Evolution      : {evo.total_cycles} cycles, {evo.average_score:.1%} success rate")
        print(f"  Sandbox engine : {config.sandbox.engine}")

    asyncio.run(_status())


def main() -> None:
    """CLI entry point — registered in pyproject.toml [project.scripts]."""
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="JARVIS — Just A Rather Very Intelligent System",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # jarvis serve
    serve_parser = subparsers.add_parser("serve", help="Start the API server")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    serve_parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    serve_parser.set_defaults(func=cmd_serve)

    # jarvis chat
    chat_parser = subparsers.add_parser("chat", help="Interactive chat mode")
    chat_parser.set_defaults(func=cmd_chat)

    # jarvis status
    status_parser = subparsers.add_parser("status", help="Display system status")
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
