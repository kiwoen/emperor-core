"""
JARVIS — Main Entry Point.

Start JARVIS with:
    python -m jarvis
Or directly:
    python main.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis.core.orchestrator import Orchestrator
from jarvis.core.config import JARVISConfig, load_config
from jarvis.memory.engine import MemoryEngine
from jarvis.evolution.controller import EvolutionController
from jarvis.sandbox import SandboxManager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("jarvis.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("jarvis")


def print_banner(config: JARVISConfig) -> None:
    """Display the JARVIS startup banner."""
    banner = f"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗                ║
║     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝                ║
║     ██║███████║██████╔╝██║   ██║██║███████╗                ║
║ ██  ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║                ║
║  ████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║                ║
║   ╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝                ║
║                                                              ║
║     Just A Rather Very Intelligent System                    ║
║     Version {config.version:<47}║
║                                                              ║
║     "At your service, sir."                                  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


async def run_cli(orchestrator: Orchestrator) -> None:
    """Run JARVIS in interactive CLI mode."""
    print("\nType 'exit' or 'quit' to stop.\n")

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


async def main() -> None:
    """Initialize and start JARVIS."""
    # Load configuration
    config = load_config()
    print_banner(config)

    # Initialize LLM engine
    from jarvis.core.llm import init_llm

    init_llm(config)

    # Initialize core subsystems
    logger.info("Initializing memory engine...")
    memory = MemoryEngine(
        persist_dir=str(config.data_dir / "memory"),
        compression_threshold=config.memory.auto_compress_threshold,
    )

    logger.info("Initializing sandbox manager...")
    sandbox = SandboxManager(
        engine=config.sandbox.engine,
        memory_limit=config.sandbox.memory_limit,
        cpu_limit=config.sandbox.cpu_limit,
        timeout_seconds=config.sandbox.timeout_seconds,
        network_enabled=config.sandbox.network_enabled,
    )

    logger.info("Initializing evolution controller...")
    evolution = EvolutionController(data_dir=config.data_dir, config=config)

    logger.info("Building orchestrator...")
    orchestrator = Orchestrator(
        memory_engine=memory,
        evolution_controller=evolution,
        sandbox_manager=sandbox,
    )

    # Load all domain modules
    logger.info("Loading domain modules...")
    orchestrator.load_all_domains()
    loaded = orchestrator.registry.list_domains()
    logger.info("Loaded %d domains: %s", len(loaded), [d.name for d in loaded])

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)

    # Start web server if enabled
    if config.web_server_enabled:
        logger.info("Starting API server on port 8000...")
        from jarvis.api.server import start_server
        server_task = asyncio.create_task(start_server(orchestrator, config))

    # Run CLI
    await run_cli(orchestrator)

    # Cleanup
    if config.web_server_enabled:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    evolution.save_state()
    sandbox.cleanup()
    logger.info("JARVIS shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
