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

from jarvis.core.integration import SystemIntegration
from jarvis.core.config import JARVISConfig, load_config


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


async def run_cli(integration: SystemIntegration) -> None:
    """Run JARVIS in interactive CLI mode via the integrated pipeline."""
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

        result = await integration.execute(user_input)
        if result["success"]:
            output = result.get("output", "")
            domain = result.get("domain", "core")
            print(f"JARVIS [{domain}] > {output}")
        else:
            error = result.get("error", "Unknown error")
            print(f"JARVIS > [ERROR] {error}")


async def main() -> None:
    """Initialize and start JARVIS using SystemIntegration."""
    # Load configuration
    config = load_config()
    print_banner(config)

    # Initialize LLM engine
    from jarvis.core.llm import init_llm
    init_llm(config)

    # Initialize core subsystems (passed into SystemIntegration)
    logger.info("Initializing memory engine...")
    from jarvis.memory.engine import MemoryEngine
    memory = MemoryEngine(
        persist_dir=str(Path(config.data_dir) / "memory"),
        compression_threshold=getattr(config.memory, "auto_compress_threshold", 5000),
    )

    logger.info("Initializing sandbox manager...")
    from jarvis.sandbox import SandboxManager
    sandbox = SandboxManager(
        engine=getattr(config.sandbox, "engine", "direct"),
        memory_limit=getattr(config.sandbox, "memory_limit", 512),
        cpu_limit=getattr(config.sandbox, "cpu_limit", 1.0),
        timeout_seconds=getattr(config.sandbox, "timeout_seconds", 30),
        network_enabled=getattr(config.sandbox, "network_enabled", False),
    )

    logger.info("Initializing evolution controller...")
    from jarvis.evolution.controller import EvolutionController
    evolution = EvolutionController(data_dir=Path(config.data_dir), config=config)

    # ── Start SystemIntegration ──────────────────────────────────────────
    # Wires Hermes + Codex + VSCode + MCP + Orchestrator in dependency order
    integration = SystemIntegration()
    await integration.start(
        memory_engine=memory,
        evolution_controller=evolution,
        sandbox_manager=sandbox,
    )

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)

    # Start API server if enabled
    server_task = None
    if config.web_server_enabled:
        logger.info("Starting API server on port 8000...")
        from jarvis.api.server import start_server
        server_task = asyncio.create_task(
            start_server(integration.orchestrator, config)
        )

    # Run interactive CLI
    await run_cli(integration)

    # Cleanup
    if server_task is not None:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    await integration.shutdown()
    evolution.save_state()
    sandbox.cleanup()
    logger.info("JARVIS shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
