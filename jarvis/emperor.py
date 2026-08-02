"""Emperor — one-line entry point for the evolutionary AI system.

Emperor bundles the Court, TaskEngine, REST API, and CLI into a single
orchestrator. Everything starts from here.

Usage:
    from jarvis.emperor import Emperor

    emp = Emperor()
    emp.register("turing", domain="math")
    emp.evolve(cycles=3)
    emp.execute_task("What is 17 * 23?", domain="math")
    emp.serve(port=9020)

Configuration:
    Emperor auto-loads ``jarvis.yaml`` (JSON-inside-YAML) if present.
    On first run, ``save_default_config()`` writes all defaults to disk.
    See ``jarvis/config.py`` for the full schema.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from jarvis.config import (
    EmperorConfig as AppConfig,
    load_config as load_app_config,
    save_default_config as save_default_app_config,
)

# Tracing
from jarvis.tracer import tracer as _tracer

logger = logging.getLogger("jarvis.emperor")


def _make_accept_callback(minister_name: str):
    """Create a simple 'always accept' handoff callback for a minister."""
    from jarvis.handoff import HandoffResult, HandoffStatus

    def _accept(req) -> HandoffResult:
        return HandoffResult(
            handoff_id=req.handoff_id,
            status=HandoffStatus.ACCEPTED,
            source_minister=req.source_minister,
            target_minister=minister_name,
            context=req.context,
        )

    return _accept


# ══════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════


@dataclass
class EmperorConfig:
    """Top-level Emperor configuration."""

    # Court
    min_ministers: int = 3
    max_ministers: int = 20
    genome_path: str = ""
    history_path: str = ""

    # Evolution
    crossover_rate: float = 0.6
    elitism_count: int = 2
    enable_auto_breeding: bool = True

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 9020
    enable_api: bool = False

    # Auto-start (serve() one-command live dashboard)
    auto_schedule: bool = True
    auto_seed_ministers: bool = True
    auto_evolve_interval_minutes: float = 5.0
    auto_evolve_cycles: int = 1
    auto_tasks_interval_minutes: float = 3.0

    # Persistence
    data_dir: str = ""

    # Logging
    log_level: str = "INFO"

    # Runtime
    max_task_timeout: float = 30.0

    # Context compression
    max_context_tokens: int = 8192
    compression_strategy: str = "auto"  # auto | summarize | extract | prune | hybrid


# ══════════════════════════════════════════════════════════════════
# Bridge: jarvis.yaml AppConfig → EmperorConfig
# ══════════════════════════════════════════════════════════════════


def _app_config_to_emperor(app: AppConfig) -> EmperorConfig:
    """Convert ``AppConfig`` (from jarvis.yaml) to runtime ``EmperorConfig``."""
    return EmperorConfig(
        min_ministers=3,
        max_ministers=app.max_ministers,
        crossover_rate=0.6,
        elitism_count=2,
        enable_auto_breeding=True,
        api_host=app.dashboard.host,
        api_port=app.dashboard.port,
        enable_api=False,
        auto_schedule=app.scheduler.auto_schedule,
        auto_seed_ministers=True,
        auto_evolve_interval_minutes=app.scheduler.evolve_interval_minutes,
        auto_evolve_cycles=1,
        auto_tasks_interval_minutes=app.scheduler.task_interval_minutes,
        data_dir="",
        log_level="INFO",
        max_task_timeout=30.0,
        max_context_tokens=getattr(app, "max_context_tokens", 8192),
        compression_strategy=getattr(app, "compression_strategy", "auto"),
    )


# ══════════════════════════════════════════════════════════════════
# Emperor
# ══════════════════════════════════════════════════════════════════


class Emperor:
    """One-stop orchestrator for the evolutionary AI system.

    >>> emp = Emperor()
    >>> emp.register("turing", domain="math")
    >>> emp.evolve(cycles=5)
    >>> emp.status()
    """

    def __init__(
        self,
        config: Optional[EmperorConfig] = None,
        config_path: Optional[str] = None,
    ) -> None:
        # Load from jarvis.yaml if no explicit EmperorConfig provided
        if config is None and config_path is None:
            app_cfg = load_app_config()
        elif config_path is not None:
            app_cfg = load_app_config(config_path)
        else:
            app_cfg = None

        if app_cfg is not None:
            self._app_config = app_cfg
            config = _app_config_to_emperor(app_cfg)

        self.config: EmperorConfig = config or EmperorConfig()

        # Defer imports for fast startup
        from jarvis.court.court import Court, CourtConfig

        court_cfg = CourtConfig(
            min_ministers=self.config.min_ministers,
            max_ministers=self.config.max_ministers,
            crossover_rate=self.config.crossover_rate,
            elitism_count=self.config.elitism_count,
            enable_auto_breeding=self.config.enable_auto_breeding,
            genome_path=self.config.genome_path or None,
        )
        self._court = Court(config=court_cfg)

        # Create default capability registry (filtered by config)
        from jarvis.capability import create_default_registry
        enabled_caps = getattr(
            getattr(self, '_app_config', None), 'capability', None
        )
        if enabled_caps is not None:
            self._capability_registry = create_default_registry(
                enabled=enabled_caps.enabled_capabilities,
            )
        else:
            self._capability_registry = create_default_registry()

        from jarvis.court.task_engine import TaskEngine

        self._task_engine = TaskEngine(
            self._court,
            capability_registry=self._capability_registry,
        )
        self._app: Any = None  # FastAPI app (lazy)
        self._scheduler: Any = None  # Scheduler (lazy)
        self._alert_manager: Any = None  # AlertManager (lazy)

        # Self-healing
        self._healing_engine: Any = None  # HealingEngine (lazy)

        # Pipeline monitoring
        self._pipeline_monitor: Any = None  # PipelineMonitor (lazy)

        # Plugin system (lifecycle hooks)
        from jarvis.plugin import LifecycleEvent, PluginManager
        self._plugin_manager: Any = PluginManager()

        # Plugin System (hot-load third-party plugins)
        from jarvis.plugin_system import PluginManager as PluginSystemManager
        self._plugin_system = PluginSystemManager()

        # Plugin marketplace
        from jarvis.plugin_marketplace import PluginMarketplace
        self._plugin_marketplace = PluginMarketplace(data_dir=self.config.data_dir)

        # Sandbox manager — secure code execution environment
        from jarvis.sandbox import SandboxManager
        self._sandbox_manager = SandboxManager(
            engine="local_subprocess",
            timeout_seconds=60,
            network_enabled=False,
        )

        # Eagerly register MetricsPlugin so every event from the very
        # first dispatch is captured.
        from jarvis.plugins import MetricsPlugin
        self._metrics_plugin: Any = MetricsPlugin()
        self._plugin_manager.register(self._metrics_plugin)

        # Audit trail — immutable execution log
        from jarvis.audit import AuditLogger
        audit_db = (Path(self.config.data_dir) / "audit.db"
                    if self.config.data_dir else
                    Path("audit.db"))
        self._audit_logger: AuditLogger = AuditLogger(str(audit_db))

        # Evals runner — regression testing
        from jarvis.eval import EvalRunner
        self._eval_runner: EvalRunner = EvalRunner(
            capability_registry=self._capability_registry, emperor=self)

        # Model router — cost-aware multi-model routing
        from jarvis.core.router import ModelRouter
        self._model_router: ModelRouter = ModelRouter()

        # Multi-model router — DeepSeek V3/R1 + parallel/ensemble/strategy routing
        from jarvis.multi_model import MultiModelRouter
        self._multi_model_router: MultiModelRouter = MultiModelRouter()

        # Cost tracker — shared with MultiModelRouter for per-invocation cost recording
        from jarvis.cost_tracker import CostTracker
        cost_data_dir = self.config.data_dir if self.config.data_dir else str(Path.cwd())
        self._cost_tracker: CostTracker = CostTracker(
            persistence_path=str(Path(cost_data_dir) / "cost_records.json"),
        )
        self._multi_model_router.cost_tracker = self._cost_tracker

        # L4 GraphRAG — knowledge-graph memory engine
        from jarvis.graph_rag import GraphRAG
        self._graph_rag: GraphRAG = GraphRAG()

        # Adaptive prompt template manager
        from jarvis.prompt_template import PromptTemplateManager
        template_data_dir = self.config.data_dir if self.config.data_dir else str(Path.cwd())
        self._template_manager: PromptTemplateManager = PromptTemplateManager(data_dir=template_data_dir)

        # Inject template_manager into capability module
        from jarvis.capability import set_template_manager
        set_template_manager(self._template_manager)

        # Context versioning & rollback — immutable state snapshots
        from jarvis.context_versioning import (
            ContextVersioning,
            create_plugin_state_provider,
            create_plugin_rollback_handler,
            create_template_state_provider,
            create_template_rollback_handler,
        )
        versioning_dir = self.config.data_dir if self.config.data_dir else str(Path.cwd())
        self._versioning: ContextVersioning = ContextVersioning(data_dir=versioning_dir)

        # Register versionable components
        self._versioning.register_component(
            "plugins",
            create_plugin_state_provider(self._plugin_marketplace),
            create_plugin_rollback_handler(self._plugin_marketplace),
        )
        self._versioning.register_component(
            "templates",
            create_template_state_provider(self._template_manager),
            create_template_rollback_handler(self._template_manager),
        )

        # Auto-snapshot on startup so there's always a baseline
        self._versioning.auto_snapshot(trigger="emperor-init")

        # HITL Approval engine — pre-execution human gate for risky ops
        from jarvis.approval import ApprovalEngine
        approval_db = (Path(self.config.data_dir) / "approval.db"
                       if self.config.data_dir else
                       Path("approval.db"))
        self._approval_engine: ApprovalEngine = ApprovalEngine(
            str(approval_db),
            audit_logger=self._audit_logger,
        )

        # MCP Manager — unified gateway for MCP Client + built-in mock servers
        from jarvis.mcp_manager import MCPManager
        self._mcp_manager: MCPManager = MCPManager()
        self._mcp_manager.register_builtin_mock_servers()
        logger.info(
            "[Emperor] MCP Manager initialized — %d servers, %d tools",
            self._mcp_manager.server_count,
            len(self._mcp_manager.get_all_tools()),
        )

        # Handoff Protocol — standardized multi-agent task handoff
        from jarvis.handoff import HandoffProtocol
        self._handoff: HandoffProtocol = HandoffProtocol(
            audit_logger=self._audit_logger,
        )

        # Reflexion Engine — self-reflection & auto-correction
        from jarvis.reflexion import ReflexionEngine
        self._reflexion_engine: ReflexionEngine = ReflexionEngine(
            threshold=0.6,
            max_retries=3,
        )

        # State Machine — LangGraph-inspired execution engine
        from jarvis.state_machine import create_dispatch_workflow
        self._state_machine = create_dispatch_workflow()
        self._state_machine_data: dict = {}

        # RBAC Engine — role-based access control for enterprise security
        from jarvis.rbac import RBACEngine
        self._rbac_engine: RBACEngine = RBACEngine()

        # Context compression engine — manages long conversation histories
        from jarvis.context_compressor import ContextCompressor
        self._context_compressor: ContextCompressor = ContextCompressor(keep_recent=4)
        self._message_history: list[dict] = []  # Accumulated conversation context

        # Post-LLM Hallucination Guard — detects unverifiable claims in LLM output
        from jarvis.hallucination_guard import HallucinationGuard, GuardMode
        self._hallucination_guard: HallucinationGuard = HallucinationGuard(
            mode=GuardMode.STRICT,
            enable_llm_verification=False,
            max_correction_rounds=3,
        )

        self._dispatch(LifecycleEvent.ON_INIT, emperor=self)

        # Load persisted state if data_dir set
        if self.config.data_dir:
            self._load_state()

        logger.info("[Emperor] initialized — %d ministers",
                    len(self._court.active_ministers))

    # ── Court proxy ────────────────────────────────────────────────

    @property
    def court(self):
        """Direct access to the underlying Court."""
        return self._court

    @property
    def app_config(self) -> Optional[AppConfig]:
        """Access to the loaded jarvis.yaml config (if available)."""
        return getattr(self, '_app_config', None)

    @property
    def task_engine(self):
        """Direct access to the TaskEngine."""
        return self._task_engine

    @property
    def plugins(self):
        """Direct access to the PluginManager."""
        return self._plugin_manager

    @property
    def capability_registry(self):
        """Direct access to the CapabilityRegistry."""
        return self._capability_registry

    @property
    def audit_logger(self):
        """Direct access to the AuditLogger."""
        return self._audit_logger

    @property
    def eval_runner(self):
        """Direct access to the EvalRunner."""
        return self._eval_runner

    @property
    def plugin_marketplace(self):
        """Direct access to the PluginMarketplace."""
        return self._plugin_marketplace

    @property
    def plugin_system(self):
        """Direct access to the PluginSystemManager (hot-load third-party plugins)."""
        return self._plugin_system

    @property
    def sandbox_manager(self):
        """Direct access to the SandboxManager."""
        return self._sandbox_manager

    @property
    def versioning(self):
        """Direct access to the ContextVersioning engine."""
        return self._versioning

    @property
    def template_manager(self):
        """Direct access to the PromptTemplateManager."""
        return self._template_manager

    @property
    def model_router(self):
        """Direct access to the ModelRouter."""
        return self._model_router

    @property
    def multi_model_router(self):
        """Direct access to the MultiModelRouter (DeepSeek + parallel/ensemble/strategy)."""
        return self._multi_model_router

    @property
    def cost_tracker(self):
        """Direct access to the CostTracker for per-invocation cost recording."""
        return self._cost_tracker

    @property
    def graph_rag(self):
        """Direct access to the L4 GraphRAG knowledge graph engine."""
        return self._graph_rag

    @property
    def approval_engine(self):
        """Direct access to the ApprovalEngine."""
        return self._approval_engine

    @property
    def mcp_manager(self):
        """Direct access to the MCPManager (MCP Client + built-in mock servers)."""
        return self._mcp_manager

    @property
    def handoff(self):
        """Direct access to the HandoffProtocol."""
        return self._handoff

    @property
    def reflexion(self):
        """Direct access to the ReflexionEngine."""
        return self._reflexion_engine

    @property
    def state_machine(self):
        """Direct access to the StateMachine execution engine."""
        return self._state_machine

    @property
    def rbac_engine(self):
        """Direct access to the RBACEngine for role-based access control."""
        return self._rbac_engine

    @property
    def context_compressor(self):
        """Direct access to the ContextCompressor."""
        return self._context_compressor

    @property
    def hallucination_guard(self):
        """Direct access to the HallucinationGuard (post-LLM hallucination detection)."""
        return self._hallucination_guard

    @property
    def message_history(self) -> list[dict]:
        """Current accumulated conversation context."""
        return self._message_history

    def clear_context(self) -> None:
        """Reset the accumulated conversation context."""
        self._message_history = []
        logger.info("[Emperor] context history cleared")

    def _dispatch(self, event: Any, **kwargs: Any) -> Any:
        """Dispatch a lifecycle event to all registered plugins."""
        return self._plugin_manager.dispatch(event, **kwargs)

    def register(self, name: str, domain: str = "general",
                 temperature: float = 0.7) -> None:
        """Register a new minister."""
        from jarvis.plugin import LifecycleEvent

        self._court.register(name, domain=domain,
                             temperature=temperature)
        self._dispatch(LifecycleEvent.ON_MINISTER_REGISTER,
                       minister_name=name, domain=domain,
                       temperature=temperature)

    def register_many(self, names: list[str], domain: str = "general",
                      temperature: float = 0.7) -> None:
        """Register multiple ministers at once."""
        specs = [{"name": n, "domain": domain, "temperature": temperature}
                 for n in names]
        self._court.register_many(specs)

    def evolve(self, cycles: int = 1) -> dict:
        """Run evolution cycles and return summary."""
        from jarvis.plugin import LifecycleEvent

        if cycles < 1:
            raise ValueError("cycles must be >= 1")
        self._dispatch(LifecycleEvent.ON_EVOLVE_START, cycles=cycles)
        try:
            result = self._court.evolve(cycles)
        except Exception as e:
            self._dispatch(LifecycleEvent.ON_TASK_ERROR, error=e,
                           context="evolve")
            raise
        self._dispatch(LifecycleEvent.ON_EVOLVE_END, result=result)
        return result

    # ── Task execution ─────────────────────────────────────────────

    def execute_task(
        self,
        prompt: str,
        *,
        domain: str = "general",
        expected: str = "",
        task_id: str = "",
        required_permission: Optional[Any] = None,
    ) -> dict:
        """Execute a single task and return outcome as dict.

        Args:
            prompt: Task prompt string.
            domain: Task domain for minister routing.
            expected: Optional expected answer for scoring.
            task_id: Optional task identifier (auto-generated if empty).
            required_permission: Optional Permission enum — if set, the
                selected minister's role is checked before execution.
                Returns HTTP 403-style error on denial.
        """
        from jarvis.court.task_engine import TaskRequest
        from jarvis.plugin import LifecycleEvent

        if not task_id:
            import uuid
            task_id = uuid.uuid4().hex[:8]

        # ── RBAC pre-dispatch check ──
        _preselected_minister: Optional[str] = None
        if required_permission is not None:
            # Pre-select minister so we can check permissions before execution
            _preselected_minister = self._task_engine._select_minister(domain)
            if not self._rbac_engine.check_permission(
                _preselected_minister, required_permission
            ):
                role = self._rbac_engine.get_role(_preselected_minister)
                logger.warning(
                    "[Emperor] RBAC denied: minister=%s role=%s permission=%s",
                    _preselected_minister, role.name, required_permission.name,
                )
                return {
                    "task_id": task_id,
                    "status": "forbidden",
                    "error": (
                        f"Permission '{required_permission.name}' denied "
                        f"for minister '{_preselected_minister}' (role: {role.name})"
                    ),
                    "minister": _preselected_minister,
                    "success": False,
                    "confidence": 0.0,
                    "merit_score": 0.0,
                    "execution_time_ms": 0.0,
                    "response": "",
                    "handoff": None,
                }

        # ── Tracing: emperor.dispatch span ──
        _trace_ctx = _tracer.start_span(
            "emperor.dispatch",
            kind="server",
            attributes={"task_id": task_id, "domain": domain, "prompt_len": len(prompt)},
        )

        _trace_status = "error"
        _trace_attrs: dict[str, Any] = {}
        try:

            req = TaskRequest(
                id=task_id,
                prompt=prompt,
                domain=domain,
                expected=expected or None,
                deadline_seconds=self.config.max_task_timeout,
            )

            self._dispatch(LifecycleEvent.ON_TASK_BEFORE,
                           task_id=task_id, prompt=prompt, domain=domain)

            # ── Context compression check ──
            # Accumulate messages and compress if exceeding token budget
            self._message_history.append({"role": "user", "content": prompt})
            if self._context_compressor is not None:
                from jarvis.context_compressor import (
                    CompressionStrategy, estimate_messages_tokens,
                )
                current_tokens = estimate_messages_tokens(self._message_history)
                if current_tokens > self.config.max_context_tokens:
                    strategy_map = {
                        "auto": None,  # let auto_compress decide
                        "summarize": CompressionStrategy.SUMMARIZE,
                        "extract": CompressionStrategy.EXTRACT,
                        "prune": CompressionStrategy.PRUNE,
                        "hybrid": CompressionStrategy.HYBRID,
                    }
                    strat = strategy_map.get(
                        self.config.compression_strategy, None
                    )
                    if strat is None:
                        result = self._context_compressor.auto_compress(
                            self._message_history,
                            max_tokens=self.config.max_context_tokens,
                        )
                    else:
                        result = self._context_compressor.compress(
                            self._message_history,
                            strategy=strat,
                            target_tokens=self.config.max_context_tokens,
                        )
                    self._message_history = result.messages
                    logger.info(
                        "[Emperor] context compressed: %d→%d tokens (strategy=%s)",
                        result.original_tokens,
                        result.compressed_tokens,
                        result.strategy,
                    )

            # ── HITL Approval check ──
            if self._approval_engine.require_approval(
                task_id=task_id, prompt=prompt, domain=domain,
            ):
                approval_req = self._approval_engine.create_request(
                    task_id=task_id, prompt=prompt, domain=domain,
                )
                _trace_status = "ok"
                _trace_attrs = {
                    "status": "pending_approval",
                    "approval_id": approval_req.id,
                    "risk_level": approval_req.risk_level,
                }
                return {
                    "task_id": task_id,
                    "status": "pending_approval",
                    "approval_id": approval_req.id,
                    "risk_level": approval_req.risk_level,
                    "message": f"Task requires human approval (risk={approval_req.risk_level}). Approval ID: {approval_req.id}",
                }

            # ── State Machine: planning → execution ──
            _sm = self._state_machine
            _sm_ctx = _sm.start("planning", data={
                "task_id": task_id, "domain": domain, "prompt_len": len(prompt),
            })
            _sm_ctx = _sm.trigger("execution", _sm_ctx)

            # ── Audit: before ──
            import time as _time
            _started = _time.time()
            self._audit_logger.log_task_before(
                trace_id=task_id, prompt=prompt, domain=domain)

            outcome = self._task_engine.execute(req, minister=_preselected_minister)

            _elapsed_ms = (_time.time() - _started) * 1000

            result = {
                "task_id": outcome.task_id,
                "minister": outcome.minister,
                "success": outcome.success,
                "confidence": outcome.confidence,
                "merit_score": outcome.merit_score,
                "execution_time_ms": outcome.execution_time_ms,
                "response": outcome.raw_response,
                "error": outcome.error,
                "handoff": None,  # populated below if a handoff occurs
            }

            # ── Post-execution handoff check ──
            # If the TaskRequest meta contains a handoff target and the minister
            # indicated a handoff is needed, execute the handoff protocol.
            if req.meta and req.meta.get("handoff_target"):
                from jarvis.handoff import (
                    HandoffRequest, HandoffContext, FallbackStrategy,
                )
                handoff_target = req.meta["handoff_target"]
                handoff_reason = req.meta.get("handoff_reason", "")
                handoff_priority = req.meta.get("handoff_priority", 2)
                fallback_str = req.meta.get("handoff_fallback", "reject")
                candidates = req.meta.get("handoff_candidates", [])

                # Create handoff context carrying forward task history
                ctx = HandoffContext(
                    task_id=task_id,
                    original_prompt=prompt,
                    priority=handoff_priority,
                )
                ctx.record_step(outcome.minister, outcome.raw_response, "completed")

                # Auto-register target minister if not already registered as handoff target
                if not self._handoff.has_target(handoff_target):
                    self._handoff.register_target(
                        handoff_target,
                        lambda req, name=handoff_target: _make_accept_callback(name),
                    )

                try:
                    strategy = FallbackStrategy(fallback_str)
                except ValueError:
                    strategy = FallbackStrategy.REJECT

                ho_req = HandoffRequest(
                    source_minister=outcome.minister,
                    target_minister=handoff_target,
                    context=ctx,
                    reason=handoff_reason,
                    priority=handoff_priority,
                    fallback_strategy=strategy,
                    candidate_ministers=candidates,
                )

                ho_result = self._handoff.handoff(ho_req)
                result["handoff"] = ho_result.to_dict()
                logger.info(
                    "[Emperor] Handoff %s: %s → %s (%s)",
                    task_id, outcome.minister, handoff_target,
                    ho_result.status.value,
                )

            if outcome.success:
                self._dispatch(LifecycleEvent.ON_TASK_AFTER, outcome=result)
                # Record assistant response in context history
                if outcome.raw_response:
                    self._message_history.append({
                        "role": "assistant",
                        "content": str(outcome.raw_response)[:2000],
                    })
            else:
                self._dispatch(LifecycleEvent.ON_TASK_ERROR,
                               task_id=task_id, error=outcome.error)

            # ── Audit: capability invocation ──
            if outcome.capability_name and outcome.capability_result:
                self._audit_logger.log_capability_invoke(
                    trace_id=task_id,
                    step=1,
                    cap_name=outcome.capability_name,
                    prompt=prompt,
                    result=str(outcome.capability_result)[:500],
                    success=True,
                    duration_ms=_elapsed_ms,
                )

            # ── Audit: after ──
            self._audit_logger.log_task_after(
                trace_id=task_id,
                step=2,
                success=outcome.success,
                result=str(outcome.raw_response or "")[:500],
                duration_ms=_elapsed_ms,
                error=str(outcome.error or ""),
            )

            # Persist task to database
            if self._court.db is not None:
                try:
                    self._court.db.save_task(
                        task_id=result["task_id"],
                        prompt=prompt,
                        minister=result["minister"],
                        result=result["response"],
                        confidence=result["confidence"],
                        status="completed" if result["success"] else "failed",
                        capability=domain,
                    )
                except Exception:
                    logger.warning("[Emperor] Failed to persist task to DB")

            # ── Reflexion: post-dispatch quality check & auto-correction ──
            _sm_ctx = _sm.trigger("reflection", _sm_ctx)
            _sm_ctx.data["confidence"] = result.get("confidence", 0)
            if result["success"] and result["confidence"] <= getattr(
                self._reflexion_engine, "threshold", 0.6
            ):
                try:
                    refl = self._reflexion_engine.reflect(
                        task_id=task_id,
                        prompt=prompt,
                        response=result.get("response", ""),
                        domain=domain,
                    )
                    result["reflexion"] = refl.to_dict()
                    if refl.corrected:
                        result["response"] = refl.corrected_response
                        result["confidence"] = max(result["confidence"], refl.confidence)
                        _sm_ctx.data["confidence"] = result["confidence"]
                        logger.info("[Emperor] Reflexion corrected task=%s conf=%.4f", task_id, refl.confidence)
                    elif refl.status.value == "failed":
                        logger.warning("[Emperor] Reflexion failed for task=%s after %d attempts", task_id, refl.attempts)
                except Exception:
                    logger.exception("[Emperor] Reflexion error for task=%s", task_id)

            # ── Post-LLM Hallucination Guard ──
            # Check LLM output for unverifiable claims before returning to user.
            if result["success"] and result.get("response"):
                try:
                    hg_result = self._hallucination_guard.check(
                        output=str(result["response"]),
                        context=f"Task: {prompt}\nDomain: {domain}",
                    )
                    result["hallucination_guard"] = hg_result.to_dict()
                    if hg_result.has_hallucinations:
                        logger.warning(
                            "[Emperor] HallucinationGuard flagged %d claims in task=%s "
                            "(confidence=%.4f)",
                            hg_result.flagged_sentences,
                            task_id,
                            hg_result.confidence,
                        )
                except Exception:
                    logger.exception(
                        "[Emperor] HallucinationGuard error for task=%s", task_id
                    )

            # ── State Machine: reflection → completion ──
            _sm_ctx = _sm.trigger("completion", _sm_ctx)
            _sm.stop()

            # ── End tracing span ──
            _trace_status = "ok" if result["success"] else "error"
            _trace_attrs = {
                "minister": result.get("minister", ""),
                "success": result["success"],
                "confidence": result.get("confidence", 0),
                "elapsed_ms": _elapsed_ms,
            }

            return result

        finally:
            _tracer.end_span(_trace_ctx.span_id, status=_trace_status, attributes=_trace_attrs)

    def execute_batch(self, tasks: list[dict]) -> list[dict]:
        """Execute a batch of tasks. Each dict: {prompt, domain?, expected?}."""
        outcomes = []
        for i, t in enumerate(tasks):
            result = self.execute_task(
                prompt=t["prompt"],
                domain=t.get("domain", "general"),
                expected=t.get("expected", ""),
                task_id=t.get("task_id", ""),
            )
            outcomes.append(result)
        return outcomes

    # ── API server ─────────────────────────────────────────────────

    def serve(self, port: int = 0, host: str = "") -> None:
        """Start the REST API server (blocking).

        One-command live dashboard — by default this will:
          1. Auto-register a default minister lineup (if none exist).
          2. Auto-start the Scheduler with periodic evolution + tasks.

        Args:
            port: Port to listen on (uses config.api_port if 0).
            host: Host to bind (uses config.api_host if empty).
        """
        if port == 0:
            port = self.config.api_port or 9020
        if not host:
            host = self.config.api_host or "127.0.0.1"

        # One-command live dashboard: seed ministers + start scheduler
        if self.config.auto_seed_ministers:
            self._ensure_default_ministers()

        # ── Initialize database persistence ────────────────────────
        import os
        from jarvis.database import Database

        db_path = os.path.join(
            self.config.court_path
            if hasattr(self.config, 'court_path') and self.config.court_path
            else os.getcwd(),
            "jarvis.db",
        )
        db = Database(db_path)
        self._court.db = db

        # Inject db into alert manager and scheduler for persistence
        self.alerts._db = db
        if self._scheduler is not None:
            self._scheduler._db = db

        if self.config.auto_schedule:
            self._auto_start_scheduler()

        from jarvis.court_api import create_app, configure_app

        app = create_app(court=self._court, eval_runner=self._eval_runner, audit_logger=self._audit_logger, template_manager=self._template_manager)
        configure_app(self.app_config)
        app.extra["host"] = host
        app.extra["port"] = port
        app.extra["emperor"] = self
        app.extra["db"] = db
        app.extra["model_router"] = self._model_router
        app.extra["multi_model_router"] = self._multi_model_router
        app.extra["cost_tracker"] = self._cost_tracker
        app.extra["graph_rag"] = self._graph_rag

        # Inject scheduler state if running
        if self._scheduler is not None:
            r = self._scheduler.report()
            app.extra["scheduler_running"] = r.state == "RUNNING"
            app.extra["scheduler_jobs"] = len(r.entries)
            app.extra["scheduler_total_runs"] = r.total_runs
            # Wire alerts + healing into scheduler for auto-recovery
            self._scheduler._alert_manager = self.alerts
            self._scheduler._healing_engine = self.healing
        else:
            app.extra["scheduler_running"] = False
            app.extra["scheduler_jobs"] = 0
            app.extra["scheduler_total_runs"] = 0

        # Store alert_manager on app for dashboard access
        app.extra["alert_manager"] = self.alerts
        # Touch metrics so the plugin is registered before serving
        _ = self.metrics
        app.extra["metrics_plugin"] = self._metrics_plugin
        app.extra["plugin_marketplace"] = self._plugin_marketplace
        app.extra["plugin_system"] = self._plugin_system
        app.extra["sandbox_manager"] = self._sandbox_manager
        app.extra["versioning"] = self._versioning
        app.extra["template_manager"] = self._template_manager
        app.extra["approval_engine"] = self._approval_engine
        app.extra["handoff"] = self._handoff
        app.extra["rbac_engine"] = self._rbac_engine
        app.extra["state_machine"] = self._state_machine

        self._app = app

        import uvicorn

        logger.info("[Emperor] API + Dashboard → http://%s:%d", host, port)
        logger.info("[Emperor] Dashboard → http://%s:%d/dashboard", host, port)
        if self._scheduler is not None and self._scheduler.state.name == "RUNNING":
            r = self._scheduler.report()
            logger.info(
                "[Emperor] Scheduler RUNNING — %d jobs, evolution every %s min, tasks every %s min",
                len(r.entries),
                self.config.auto_evolve_interval_minutes,
                self.config.auto_tasks_interval_minutes,
            )
        uvicorn.run(app, host=host, port=port)

    # ── One-command live dashboard helpers ────────────────────────

    # Default minister lineup — used when jarvis.yaml is absent.
    _FALLBACK_MINISTERS: list[tuple[str, str]] = [
        ("turing",   "math"),
        ("curie",    "science"),
        ("hinton",   "code"),
        ("hippocrates", "medicine"),
        ("confucius",   "language"),
        ("tesla",    "engineering"),
        ("franklin", "research"),
        ("lovelace", "general"),
    ]

    @property
    def DEFAULT_MINISTERS(self) -> list[tuple[str, str]]:
        """Backward-compatible accessor for seed ministers.

        Returns tuples from jarvis.yaml if present, otherwise falls back
        to _FALLBACK_MINISTERS.
        """
        if self.app_config is not None and self.app_config.seed_ministers:
            return [(m["name"], m["domain"]) for m in self.app_config.seed_ministers]
        return self._FALLBACK_MINISTERS

    def _ensure_default_ministers(self) -> int:
        """Auto-register a default minister lineup if the court is empty.

        Uses ``seed_ministers`` from ``jarvis.yaml`` if present, otherwise
        falls back to ``_FALLBACK_MINISTERS``.

        Returns:
            Number of new ministers actually registered (0 if court
            was already populated).
        """
        existing = set(self._court.active_ministers)

        # Honour jarvis.yaml seed_ministers
        seed = None
        if self.app_config is not None:
            raw = self.app_config.seed_ministers
            if raw:
                seed = [(m["name"], m["domain"]) for m in raw]

        if seed is None:
            seed = self._FALLBACK_MINISTERS

        seeded: list[str] = []
        for name, domain in seed:
            if name not in existing and len(self._court.active_ministers) < self.config.max_ministers:
                try:
                    self.register(name, domain=domain, temperature=0.7)
                    seeded.append(name)
                except Exception as e:  # pragma: no cover - safety net
                    logger.warning("[Emperor] seed register %s failed: %s", name, e)
        if seeded:
            logger.info("[Emperor] auto-seeded %d ministers: %s",
                        len(seeded), ", ".join(seeded))
        return len(seeded)

    def _auto_start_scheduler(self) -> bool:
        """Start the Scheduler with periodic evolution + tasks.

        No-op if the scheduler is already running or no ministers exist.
        Immediately runs the first evolution + task batch so the dashboard
        has live data from the moment the server boots.

        Returns:
            True if scheduler was started, False otherwise.
        """
        sched = self.scheduler
        if sched.state.name == "RUNNING":
            return False
        if not self._court.active_ministers:
            return False

        # Schedule periodic evolution + tasks.
        sched.schedule_evolution(
            interval_minutes=self.config.auto_evolve_interval_minutes,
            cycles=self.config.auto_evolve_cycles,
        )
        task_templates = [
            {"prompt": "现在几点了？今天是星期几？", "domain": "general"},       # → datetime
            {"prompt": "计算 (17 * 23) + (45 / 9) - 8", "domain": "math"},      # → math
            {"prompt": "掷一个1到100的骰子，再生成3个0-1之间的随机小数", "domain": "general"},  # → random
            {"prompt": "把 'Hello Emperor Core' 反转并统计字符数", "domain": "general"},     # → text
            {"prompt": "查看 jarvis/emperor.py 文件的行数和文件大小", "domain": "code"},      # → file_info
            {"prompt": "查询北京的天气", "domain": "network"},                                 # → weather
            {"prompt": "查询上海的天气和温度", "domain": "network"},                           # → weather
            {"prompt": "查询最新科技新闻", "domain": "network"},                                # → news
            {"prompt": "今天有什么重要新闻", "domain": "network"},                              # → news
            {"prompt": "搜索一下 Python 3.12 的新特性", "domain": "general"},                  # → web_search
            {"prompt": "搜索天气预报相关的新闻", "domain": "general"},                         # → web_search
        ]
        sched.schedule_tasks(
            interval_minutes=self.config.auto_tasks_interval_minutes,
            templates=task_templates,
        )

        # Wire emperor reference for built-in alert rule evaluation
        sched.emperor = self

        # Register built-in alert rules so Dashboard shows alerts on boot
        self.alerts.ensure_builtin_rules(self)

        sched.start()
        logger.info(
            "[Emperor] auto-scheduler started: evolve every %.1f min, tasks every %.1f min",
            self.config.auto_evolve_interval_minutes,
            self.config.auto_tasks_interval_minutes,
        )

        # ── Immediate first run so dashboard shows live data on boot ──
        try:
            logger.info("[Emperor] running first evolution (%d cycles) …",
                        self.config.auto_evolve_cycles)
            self.evolve(cycles=self.config.auto_evolve_cycles)
        except Exception:
            logger.exception("[Emperor] first evolution failed")

        try:
            logger.info("[Emperor] running first task batch (%d tasks) …",
                        len(task_templates))
            self.execute_batch(task_templates)
        except Exception:
            logger.exception("[Emperor] first task batch failed")

        return True

    @property
    def app(self):
        """Lazy-loaded FastAPI app (for testing)."""
        if self._app is None:
            from jarvis.court_api import create_app
            self._app = create_app(court=self._court)
            self._app.extra.setdefault("host", self.config.api_host or "127.0.0.1")
            self._app.extra.setdefault("port", self.config.api_port or 9020)
            self._app.extra["emperor"] = self
        return self._app

    # ── Status / Dashboard ─────────────────────────────────────────

    def status(self) -> dict:
        """Return a comprehensive system status snapshot."""
        try:
            ranking = self._court.merit_ranking
            top_minister = ranking[0] if ranking else None
        except Exception:
            top_minister = None

        engine_summary = self._task_engine.summary()

        return {
            "version": "1.0",
            "court": {
                "active_ministers": len(self._court.active_ministers),
                "total_ministers": len(self._court.active_ministers),
                "cycle": self._court.cycle,
                "top_minister": str(top_minister) if top_minister else "none",
            },
            "tasks": {
                "total": engine_summary["total_tasks"],
                "completed": engine_summary["completed"],
                "failed": engine_summary["failed"],
                "success_rate": engine_summary["success_rate"],
                "avg_merit": engine_summary["avg_merit"],
            },
            "config": {
                "min_ministers": self.config.min_ministers,
                "max_ministers": self.config.max_ministers,
                "crossover_rate": self.config.crossover_rate,
                "api_port": self.config.api_port,
                "data_dir": self.config.data_dir or "none",
            },
        }

    def dashboard(self) -> str:
        """Return a human-readable dashboard string."""
        s = self.status()
        lines = [
            "=" * 48,
            "  Emperor Evolution Dashboard",
            "=" * 48,
            f"  Ministers : {s['court']['active_ministers']} active",
            f"  Cycle     : {s['court']['cycle']}",
            f"  Top       : {s['court']['top_minister']}",
            f"  Tasks     : {s['tasks']['total']} total "
            f"({s['tasks']['completed']} done, {s['tasks']['failed']} failed)",
            f"  Success   : {s['tasks']['success_rate']:.1%}",
            f"  Avg Merit : {s['tasks']['avg_merit']:.1f}",
            "=" * 48,
        ]
        return "\n".join(lines)

    # ── Persistence ────────────────────────────────────────────────

    def _load_state(self) -> None:
        """Load persisted state during init if data_dir is set."""
        target = Path(self.config.data_dir)
        if not target.is_dir():
            return

        genomes_file = target / "genomes.json"
        if genomes_file.exists():
            self._court.load_genomes(str(genomes_file))
        history_file = target / "history.json"
        if history_file.exists():
            self._court.load_history(str(history_file))

    def save(self, path: str = "") -> str:
        """Save all state (genomes + history) to disk."""
        target = Path(path) if path else (
            Path(self.config.data_dir) if self.config.data_dir
            else Path.cwd() / "emperor_data"
        )
        target.mkdir(parents=True, exist_ok=True)

        # Save genomes to a file in the target directory
        self._court._sm._genome_path = str(target / "genomes.json")
        self._court.save_genomes()
        self._court.save_history(str(target / "history.json"))
        logger.info("[Emperor] state saved → %s", target)
        return str(target)

    def load(self, path: str) -> None:
        """Load state from a directory."""
        target = Path(path)
        if not target.is_dir():
            raise FileNotFoundError(f"Data dir not found: {path}")

        genomes_file = target / "genomes.json"
        if genomes_file.exists():
            self._court.load_genomes(str(genomes_file))
        history_file = target / "history.json"
        if history_file.exists():
            self._court.load_history(str(history_file))

        logger.info("[Emperor] state loaded from %s", target)

    # ── Shutdown ───────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Graceful shutdown — stop scheduler, save state, clean up."""
        from jarvis.plugin import LifecycleEvent

        self._dispatch(LifecycleEvent.ON_SHUTDOWN, emperor=self)
        if self._scheduler is not None:
            self._scheduler.stop()
        if self.config.data_dir:
            self.save()
        logger.info("[Emperor] shutdown complete")

    # ── Scheduler ──────────────────────────────────────────────────

    @property
    def scheduler(self):
        """Lazy-loaded scheduler for periodic automation."""
        if self._scheduler is None:
            from jarvis.court.scheduler import Scheduler
            db = getattr(self, '_db', None)
            self._scheduler = Scheduler(self, db=db)
        return self._scheduler

    def start_auto_evolve(self, every_minutes: float = 30,
                          cycles: int = 3) -> None:
        """Start automatic periodic evolution.

        Equivalent to: emp.scheduler.schedule_evolution(...); emp.scheduler.start()
        """
        self.scheduler.schedule_evolution(every_minutes, cycles)
        self.scheduler.start()

    def start_auto_tasks(self, every_minutes: float = 5,
                         templates: Optional[list[dict]] = None) -> None:
        """Start automatic periodic task execution.

        Equivalent to: emp.scheduler.schedule_tasks(...); emp.scheduler.start()
        """
        self.scheduler.schedule_tasks(every_minutes, templates)
        self.scheduler.start()

    # ── Alerts ─────────────────────────────────────────────────────

    @property
    def alerts(self):
        """Lazy-loaded AlertManager for health monitoring."""
        if self._alert_manager is None:
            from jarvis.alerts import AlertManager
            self._alert_manager = AlertManager()
        return self._alert_manager

    @property
    def metrics(self):
        """MetricsPlugin for performance telemetry (auto-registered on init)."""
        if self._metrics_plugin is None:
            # Should never happen — registered in __init__
            from jarvis.plugins import MetricsPlugin
            self._metrics_plugin = MetricsPlugin()
            self._plugin_manager.register(self._metrics_plugin)
        return self._metrics_plugin

    @property
    def healing(self):
        """Lazy-loaded HealingEngine for automatic recovery."""
        if self._healing_engine is None:
            from jarvis.healing import HealingEngine
            self._healing_engine = HealingEngine()
            self._register_default_healing_actions()
        return self._healing_engine

    def pipeline_monitor(self):
        """Lazy-loaded PipelineMonitor for real-time DAG visualization."""
        if self._pipeline_monitor is None:
            from jarvis.pipeline_monitor import PipelineMonitor
            from jarvis.pipeline import pipeline_registry
            self._pipeline_monitor = PipelineMonitor()
            self._pipeline_monitor.attach(pipeline_registry)
            logger.info("[Emperor] Pipeline monitor attached")
        return self._pipeline_monitor

    def _register_default_healing_actions(self) -> None:
        """Register pre‑baked healing actions on first access."""
        from jarvis.healing import HealingAction
        from jarvis.healing_actions import (
            emergency_evolve, flush_logs, gc_collect,
            replenish_ministers, reset_task_engine,
            restart_scheduler, silence_alert_rule, stop_scheduler,
        )
        engine = self._healing_engine
        engine.register(HealingAction(
            name="restart_scheduler_if_stopped",
            alert_rule="scheduler_down",
            action=lambda: restart_scheduler(),
            cooldown_seconds=60,
            tags=["scheduler"],
        ))
        engine.register(HealingAction(
            name="emergency_evolve_on_minister_loss",
            alert_rule="low_ministers",
            action=lambda: replenish_ministers(min_count=self.config.min_ministers),
            cooldown_seconds=120,
            tags=["court"],
        ))
        engine.register(HealingAction(
            name="reset_task_engine_on_stall",
            alert_rule="task_stall",
            action=lambda: reset_task_engine(),
            cooldown_seconds=300,
            tags=["task_engine"],
        ))
        engine.register(HealingAction(
            name="silence_flooding_alerts",
            alert_rule="alert_flood",
            action=lambda: silence_alert_rule("alert_flood", duration_seconds=600),
            cooldown_seconds=900,
            tags=["alerts"],
        ))
        engine.register(HealingAction(
            name="periodic_gc_collect",
            alert_rule="high_memory",
            action=lambda: gc_collect(),
            cooldown_seconds=60,
            tags=["system"],
        ))
        logger.info("[Emperor] Registered %d default healing actions",
                    len(engine.list_actions()))
