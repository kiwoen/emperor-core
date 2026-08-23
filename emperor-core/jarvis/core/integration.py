"""
JARVIS System Integration — wires all subsystems together.

This module is the central nervous system of JARVIS. It creates and
connects the MessageBus, CodexEngine, VSCodeBridge, HermesMCP, and
the Orchestrator into a cohesive runtime.

Startup order:
    1. MessageBus               — transport layer
    2. CodexEngine              — code intelligence (subscribes to codex.*)
    3. VSCodeBridge             — editor bridge (subscribes to vscode.*)
    4. HermesMCPServer          — exposes Hermes to external MCP clients
    5. HermesMCPClient          — connects Hermes to external MCP servers
    6. Orchestrator             — master controller (routes through bus)
    7. KnowledgeGraph           — cross-domain semantic graph (auto-ingestion)
    8. ImperialCourt            — Emperor + 8 ministers (court-mode dispatch)

Shutdown: reverse order, graceful cancellation of pending operations.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger("jarvis.integration")


class SystemIntegration:
    """Central wiring hub for all JARVIS subsystems.

    Usage:
        integration = SystemIntegration()
        await integration.start()

        # Now all subsystems are connected and running:
        # - orchestrator.execute("帮我分析这段代码") → CodexEngine via Hermes
        # - orchestrator.execute("打开 VSCode 并格式化文档") → VSCodeBridge via Hermes

        await integration.shutdown()
    """

    def __init__(self) -> None:
        self._bus: Any = None
        self._codex_engine: Any = None
        self._vscode_bridge: Any = None
        self._hermes_server: Any = None
        self._hermes_client: Any = None
        self._orchestrator: Any = None
        self._knowledge_graph: Any = None
        self._imperial_court: Any = None
        self._running = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def bus(self) -> Any:
        if self._bus is None:
            raise RuntimeError("Integration not started — call start() first")
        return self._bus

    @property
    def orchestrator(self) -> Any:
        if self._orchestrator is None:
            raise RuntimeError("Integration not started — call start() first")
        return self._orchestrator

    @property
    def codex(self) -> Any:
        return self._codex_engine

    @property
    def vscode(self) -> Any:
        return self._vscode_bridge

    @property
    def running(self) -> bool:
        return self._running

    @property
    def knowledge_graph(self) -> Any:
        if self._knowledge_graph is None:
            raise RuntimeError("Integration not started — call start() first")
        return self._knowledge_graph

    @property
    def imperial_court(self) -> Any:
        return self._imperial_court

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        memory_engine: Any = None,
        evolution_controller: Any = None,
        sandbox_manager: Any = None,
    ) -> None:
        """Start all subsystems in dependency order.

        Args:
            memory_engine: Optional MemoryEngine instance
            evolution_controller: Optional EvolutionController instance
            sandbox_manager: Optional SandboxManager instance
        """
        if self._running:
            logger.warning("SystemIntegration already running")
            return

        logger.info("=" * 50)
        logger.info("JARVIS System Integration — starting subsystems")
        logger.info("=" * 50)

        # ── Phase 1: Message Bus ─────────────────────────────────────────
        logger.info("[1/6] Starting MessageBus ...")
        from jarvis.hermes.bus import MessageBus
        self._bus = MessageBus()
        await self._bus.start()
        logger.info("  ✓ MessageBus ready (%d subscribers)",
                     self._bus.subscriber_count)

        # ── Phase 2: Codex Engine ────────────────────────────────────────
        logger.info("[2/6] Starting CodexEngine ...")
        from jarvis.codex.analyzer import Analyzer
        from jarvis.codex.generator import Generator
        from jarvis.codex.engine import CodexEngine
        analyzer = Analyzer()
        generator = Generator()
        self._codex_engine = CodexEngine(self._bus, analyzer, generator)
        await self._codex_engine.start()
        logger.info("  ✓ CodexEngine ready (%d subscribers)",
                     self._bus.subscriber_count)

        # ── Phase 3: VSCode Bridge ───────────────────────────────────────
        logger.info("[3/6] Starting VSCodeBridge ...")
        from jarvis.vscode.commands import VSCodeCommands
        from jarvis.vscode.bridge import VSCodeBridge
        commands = VSCodeCommands(code_cli="code")
        self._vscode_bridge = VSCodeBridge(self._bus, commands, backend="extension")
        await self._vscode_bridge.start()
        logger.info("  ✓ VSCodeBridge ready (%d subscribers)",
                     self._bus.subscriber_count)

        # ── Phase 4: Hermes MCP Server ───────────────────────────────────
        logger.info("[4/6] Starting HermesMCPServer ...")
        from jarvis.hermes_agent.server import HermesMCPServer
        self._hermes_server = HermesMCPServer(self._bus)
        # Server doesn't need explicit start — it's passive, driven by MCP
        logger.info("  ✓ HermesMCPServer ready (4 tools exposed)")

        # ── Phase 5: Hermes MCP Client ───────────────────────────────────
        logger.info("[5/6] Starting HermesMCPClient ...")
        from jarvis.hermes_agent.client import HermesMCPClient
        self._hermes_client = HermesMCPClient(self._bus)
        await self._hermes_client.start()
        logger.info("  ✓ HermesMCPClient ready")

        # ── Phase 6: Orchestrator ────────────────────────────────────────
        logger.info("[6/7] Starting Orchestrator ...")
        from jarvis.core.orchestrator import Orchestrator
        self._orchestrator = Orchestrator(
            memory_engine=memory_engine,
            evolution_controller=evolution_controller,
            sandbox_manager=sandbox_manager,
        )
        self._orchestrator.load_all_domains()
        self._orchestrator.bus = self._bus  # Inject bus for cross-domain routing
        logger.info("  ✓ Orchestrator ready (%d domains loaded)",
                     len(self._orchestrator.registry.list_domains()))

        # ── Phase 7: KnowledgeGraph ──────────────────────────────────────
        logger.info("[7/7] Starting KnowledgeGraph ...")
        from jarvis.knowledge.graph import KnowledgeGraph
        self._knowledge_graph = KnowledgeGraph()
        kg_summary = self._knowledge_graph.summary()
        logger.info("  ✓ KnowledgeGraph ready (%d entities, %d edges)",
                     kg_summary["entity_count"], kg_summary["edge_count"])

        # ── Phase 8: Imperial Court ──────────────────────────────────────
        logger.info("[8/8] Convening Imperial Court ...")
        from jarvis.court.emperor import ImperialCourt
        self._imperial_court = ImperialCourt(
            bus=self._bus,
            knowledge_graph=self._knowledge_graph,
        )
        self._imperial_court.install_ministers_from_factory()
        court_metrics = self._imperial_court.get_court_metrics()
        logger.info("  ✓ Imperial Court convened (%d ministers)",
                     court_metrics["minister_count"])

        # Wire court into orchestrator (enables court mode on demand)
        self._orchestrator.imperial_court = self._imperial_court

        self._running = True
        logger.info("=" * 50)
        logger.info("JARVIS System Integration — ALL SYSTEMS GO")
        logger.info("=" * 50)

    async def shutdown(self) -> None:
        """Graceful shutdown in reverse dependency order.

        Cancels pending operations, closes connections, stops processes.
        """
        if not self._running:
            return

        logger.info("JARVIS System Integration — shutting down ...")

        # Shutdown in reverse order
        components = [
            ("KnowledgeGraph", None),  # no async shutdown — passive in-memory graph
            ("ImperialCourt", None),   # no async shutdown — in-memory state only
            ("Orchestrator", None),  # no explicit shutdown needed
            ("HermesMCPClient", self._hermes_client.shutdown() if self._hermes_client else None),
            ("HermesMCPServer", None),  # passive — no lifecycle
            ("VSCodeBridge", self._vscode_bridge.shutdown() if self._vscode_bridge else None),
            ("CodexEngine", self._codex_engine.shutdown() if self._codex_engine else None),
            ("MessageBus", self._bus.shutdown() if self._bus else None),
        ]

        for name, coro in reversed(components):
            if coro is not None:
                try:
                    logger.debug("  Stopping %s ...", name)
                    await coro
                except Exception:
                    logger.exception("  ✗ %s shutdown error", name)
                else:
                    logger.debug("  ✓ %s stopped", name)

        self._running = False
        logger.info("JARVIS System Integration — shut down complete")

    # ------------------------------------------------------------------
    # Convenience: one-shot execute
    # ------------------------------------------------------------------

    async def execute(self, user_input: str) -> dict:
        """Execute a user command through the full integrated pipeline.

        Returns a dict with:
            - success: bool
            - output: str
            - domain: str
            - execution_time_ms: float
        """
        if not self._orchestrator:
            raise RuntimeError("Integration not started")

        result = await self._orchestrator.execute(user_input)

        # Auto-ingest into KnowledgeGraph for cross-domain pattern learning
        if self._knowledge_graph and result.success:
            domain = result.domain.name if hasattr(result.domain, "name") else str(result.domain)
            await self._knowledge_graph.ingest(user_input, domain=domain)

        return {
            "success": result.success,
            "output": str(result.output) if result.output else "",
            "domain": result.domain.name,
            "execution_time_ms": result.execution_time_ms,
            "error": result.error,
        }

    # ------------------------------------------------------------------
    # Status / Health Check
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return a health-check summary of all subsystems."""
        kg_summary = self._knowledge_graph.summary() if self._knowledge_graph else {}
        court_metrics = self._imperial_court.get_court_metrics() if self._imperial_court else {}
        return {
            "running": self._running,
            "bus": {
                "subscribers": self._bus.subscriber_count if self._bus else 0,
                "messages": self._bus.message_count if self._bus else 0,
            },
            "codex": self._codex_engine is not None,
            "vscode": self._vscode_bridge is not None,
            "hermes_server": self._hermes_server is not None,
            "hermes_client": self._hermes_client is not None,
            "orchestrator": {
                "loaded": self._orchestrator is not None,
                "domains": len(self._orchestrator.registry.list_domains()) if self._orchestrator else 0,
                "execution_mode": self._orchestrator.execution_mode.name if self._orchestrator else "N/A",
            },
            "knowledge_graph": {
                "loaded": self._knowledge_graph is not None,
                "entities": kg_summary.get("entity_count", 0),
                "edges": kg_summary.get("edge_count", 0),
            },
            "imperial_court": {
                "loaded": self._imperial_court is not None,
                "ministers": court_metrics.get("minister_count", 0),
                "decrees": court_metrics.get("decree_count", 0),
                "recent_success_rate": court_metrics.get("recent_success_rate", 0),
            },
            "providers": self._get_provider_status(),
        }

    def topic_summary(self) -> dict:
        """Return the current Hermes topic subscription map."""
        if self._bus:
            return self._bus.topic_summary()
        return {}

    def _get_provider_status(self) -> dict:
        """Return model provider availability for all ministers."""
        try:
            from jarvis.court.providers.registry import get_provider_registry
            registry = get_provider_registry()
            return registry.get_status()
        except Exception:
            return {}
