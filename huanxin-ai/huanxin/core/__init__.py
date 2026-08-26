"""Huanxin — one-line entry point for the evolutionary AI system.

Huanxin bundles the Court, TaskEngine, REST API, and CLI into a single
orchestrator. Everything starts from here.

Usage:
    from huanxin.core import Huanxin

    emp = Huanxin()
    emp.register("turing", domain="math")
    emp.evolve(cycles=3)
    emp.execute_task("What is 17 * 23?", domain="math")
    emp.serve(port=9020)

Configuration:
    Huanxin auto-loads ``huanxin.yaml`` (JSON-inside-YAML) if present.
    On first run, ``save_default_config()`` writes all defaults to disk.
    See ``huanxin/config.py`` for the full schema.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from huanxin.config import (
    HuanxinConfig as AppConfig,
    load_config as load_app_config,
    save_default_config as save_default_app_config,
)

# Tracing
from huanxin.tracer import tracer as _tracer

logger = logging.getLogger("huanxin.core")


def _make_accept_callback(minister_name: str):
    """Create a simple 'always accept' handoff callback for a minister."""
    from huanxin.handoff import HandoffResult, HandoffStatus

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


def resolve_data_dir() -> str:
    """解析持久化根目录：读环境变量 ``HUANXIN_DATA_DIR``，未设置返回 ""。

    返回 "" 表示"保持历史行为"——各组件各自回退到当前工作目录（CWD），
    这样本机开发与既有测试完全不受影响。容器镜像里由 Dockerfile 设置
    ``HUANXIN_DATA_DIR=/app/data``（挂载命名卷），从而实现数据持久化。

    Returns:
        绝对/相对目录字符串；"" 表示未配置。
    """
    return os.environ.get("HUANXIN_DATA_DIR", "").strip()


def resolve_court_path() -> str:
    """解析 ``huanxin.db`` 所在目录：``HUANXIN_COURT_PATH`` > ``HUANXIN_DATA_DIR`` > ""。

    Returns:
        目录字符串；"" 表示未配置（回退 CWD，保持历史行为）。
    """
    court = os.environ.get("HUANXIN_COURT_PATH", "").strip()
    if court:
        return court
    return resolve_data_dir()


def ensure_dir(path: str) -> str:
    """确保目录存在（幂等）。空字符串直接原样返回，不做任何事。

    Args:
        path: 目录路径，可为空字符串。

    Returns:
        传入的 path（便于链式调用）。
    """
    if not path:
        return path
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:  # 只降级告警，不让启动直接崩
        logger.warning("[Huanxin] 无法创建数据目录 %s: %s", path, exc)
    return path


@dataclass
class HuanxinConfig:
    """Top-level Huanxin configuration."""

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
    # 说明：这里保留 127.0.0.1:9020 的历史默认值（库调用者行为不变）；
    # `huanxin serve` 命令行会用 HUANXIN_HOST/HUANXIN_PORT 或 0.0.0.0:8000
    # 覆盖它（见 huanxin/cli.py:cmd_serve）。

    # Auto-start (serve() one-command live dashboard)
    auto_schedule: bool = True
    auto_seed_ministers: bool = True
    auto_evolve_interval_minutes: float = 5.0
    auto_evolve_cycles: int = 1
    auto_tasks_interval_minutes: float = 3.0

    # Persistence
    # data_dir：audit.db / approval.db / cost_records.json / outcome_records.json
    #           / 版本快照 / 提示词模板 的落盘根目录。
    # court_path：huanxin.db（法庭主库）所在目录。
    # 两者默认从 HUANXIN_DATA_DIR / HUANXIN_COURT_PATH 读取；未设置时为 ""，
    # 沿用历史行为（回退到 CWD）。容器里 Dockerfile 会设成 /app/data。
    data_dir: str = field(default_factory=resolve_data_dir)
    court_path: str = field(default_factory=resolve_court_path)

    # Logging
    log_level: str = "INFO"

    # Runtime
    max_task_timeout: float = 30.0

    # Context compression
    max_context_tokens: int = 8192
    compression_strategy: str = "auto"  # auto | summarize | extract | prune | hybrid


# ══════════════════════════════════════════════════════════════════
# Bridge: huanxin.yaml AppConfig → HuanxinConfig
# ══════════════════════════════════════════════════════════════════


def _app_config_to_emperor(app: AppConfig) -> HuanxinConfig:
    """Convert ``AppConfig`` (from huanxin.yaml) to runtime ``HuanxinConfig``."""
    return HuanxinConfig(
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
        # huanxin.yaml 不描述部署路径，因此持久化目录仍由环境变量决定，
        # 否则容器里的 HUANXIN_DATA_DIR 会被这里的空字符串覆盖掉。
        data_dir=resolve_data_dir(),
        court_path=resolve_court_path(),
        log_level="INFO",
        max_task_timeout=30.0,
        max_context_tokens=getattr(app, "max_context_tokens", 8192),
        compression_strategy=getattr(app, "compression_strategy", "auto"),
    )


# ══════════════════════════════════════════════════════════════════
# Huanxin
# ══════════════════════════════════════════════════════════════════


class Huanxin:
    """One-stop orchestrator for the evolutionary AI system.

    >>> emp = Huanxin()
    >>> emp.register("turing", domain="math")
    >>> emp.evolve(cycles=5)
    >>> emp.status()
    """

    def __init__(
        self,
        config: Optional[HuanxinConfig] = None,
        config_path: Optional[str] = None,
    ) -> None:
        # Load from huanxin.yaml if no explicit HuanxinConfig provided
        if config is None and config_path is None:
            app_cfg = load_app_config()
        elif config_path is not None:
            app_cfg = load_app_config(config_path)
        else:
            app_cfg = None

        if app_cfg is not None:
            self._app_config = app_cfg
            config = _app_config_to_emperor(app_cfg)

        self.config: HuanxinConfig = config or HuanxinConfig()

        # 持久化目录必须先存在，否则下面 AuditLogger/ApprovalEngine/
        # CostTracker 打开 sqlite/json 会直接 FileNotFoundError（容器首启
        # 挂空卷就是这个场景）。空字符串 = 用 CWD，ensure_dir 会跳过。
        ensure_dir(self.config.data_dir)
        ensure_dir(getattr(self.config, "court_path", ""))

        # Defer imports for fast startup
        from huanxin.court.court import Court, CourtConfig

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
        from huanxin.capability import create_default_registry
        enabled_caps = getattr(
            getattr(self, '_app_config', None), 'capability', None
        )
        if enabled_caps is not None:
            self._capability_registry = create_default_registry(
                enabled=enabled_caps.enabled_capabilities,
            )
        else:
            self._capability_registry = create_default_registry()

        from huanxin.court.task_engine import TaskEngine

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
        from huanxin.plugin import LifecycleEvent, PluginManager
        self._plugin_manager: Any = PluginManager()

        # Plugin System (hot-load third-party plugins)
        from huanxin.plugin_system import PluginManager as PluginSystemManager
        self._plugin_system = PluginSystemManager()

        # Plugin marketplace
        from huanxin.plugin_marketplace import PluginMarketplace
        self._plugin_marketplace = PluginMarketplace(data_dir=self.config.data_dir)

        # Sandbox manager — secure code execution environment
        from huanxin.sandbox import SandboxManager
        self._sandbox_manager = SandboxManager(
            engine="local_subprocess",
            timeout_seconds=60,
            network_enabled=False,
        )

        # Eagerly register MetricsPlugin so every event from the very
        # first dispatch is captured.
        from huanxin.plugins import MetricsPlugin
        self._metrics_plugin: Any = MetricsPlugin()
        self._plugin_manager.register(self._metrics_plugin)

        # Audit trail — immutable execution log
        from huanxin.audit import AuditLogger
        audit_db = (Path(self.config.data_dir) / "audit.db"
                    if self.config.data_dir else
                    Path("audit.db"))
        self._audit_logger: AuditLogger = AuditLogger(str(audit_db))

        # Evals runner — regression testing
        from huanxin.eval import EvalRunner
        self._eval_runner: EvalRunner = EvalRunner()

        # Model router — cost-aware multi-model routing
        from huanxin.core.router import ModelRouter
        self._model_router: ModelRouter = ModelRouter()

        # Multi-model router — DeepSeek V3/R1 + parallel/ensemble/strategy routing
        from huanxin.multi_model import MultiModelRouter
        self._multi_model_router: MultiModelRouter = MultiModelRouter()

        # Cost tracker — shared with MultiModelRouter for per-invocation cost recording
        from huanxin.cost_tracker import CostTracker
        cost_data_dir = self.config.data_dir if self.config.data_dir else str(Path.cwd())
        self._cost_tracker: CostTracker = CostTracker(
            persistence_path=str(Path(cost_data_dir) / "cost_records.json"),
        )
        self._multi_model_router.cost_tracker = self._cost_tracker

        # P2.9 Smart Routing — capability-aware routing with fallback chains
        # P0.4: an unavailable router used to be swallowed silently, which is
        # how "smart routing" shipped as a permanently-dead code path.  A
        # missing router is now a loud, explicit degradation.
        try:
            from huanxin.model_router import SmartRouter
            config_path = str(Path(self.config.data_dir or ".") / ".." / "config" / "model_routing.yaml")
            if not os.path.isfile(config_path):
                config_path = str(Path.cwd() / "config" / "model_routing.yaml")
            if not os.path.isfile(config_path):
                config_path = None  # Use defaults
            self._smart_router: Any = SmartRouter(config_path=config_path)
        except ImportError:
            self._smart_router = None
            logger.error(
                "[Huanxin] SmartRouter 缺失，路由降级为功勋第一 "
                "(huanxin.model_router import failed — minister selection "
                "falls back to merit ranking)",
                exc_info=True,
            )

        # P0.4/P0.5: hand the router to the TaskEngine so minister selection
        # actually consumes the routing decision instead of ignoring it.
        self._task_engine.set_router(self._smart_router)

        # Cost-per-successful-run tracker
        from huanxin.cost_per_success import CostPerSuccessTracker
        self._cost_per_success: CostPerSuccessTracker = CostPerSuccessTracker(
            baseline_cost_per_success=getattr(
                self.config, "cost_per_success_baseline", 0.05,
            ),
            persistence_path=str(Path(cost_data_dir) / "outcome_records.json"),
        )

        # L4 GraphRAG — knowledge-graph memory engine
        from huanxin.graph_rag import GraphRAG
        self._graph_rag: GraphRAG = GraphRAG()

        # Adaptive prompt template manager
        from huanxin.prompt_template import PromptTemplateManager
        template_data_dir = self.config.data_dir if self.config.data_dir else str(Path.cwd())
        self._template_manager: PromptTemplateManager = PromptTemplateManager(data_dir=template_data_dir)

        # Inject template_manager into capability module
        from huanxin.capability import set_template_manager
        set_template_manager(self._template_manager)

        # Context versioning & rollback — immutable state snapshots
        from huanxin.context_versioning import (
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
        from huanxin.approval import ApprovalEngine
        approval_db = (Path(self.config.data_dir) / "approval.db"
                       if self.config.data_dir else
                       Path("approval.db"))
        self._approval_engine: ApprovalEngine = ApprovalEngine(
            str(approval_db),
            audit_logger=self._audit_logger,
        )

        # MCP Manager — unified gateway for MCP Client + built-in mock servers
        from huanxin.mcp_manager import MCPManager
        self._mcp_manager: MCPManager = MCPManager()
        self._mcp_manager.register_builtin_mock_servers()
        logger.info(
            "[Huanxin] MCP Manager initialized — %d servers, %d tools",
            self._mcp_manager.server_count,
            len(self._mcp_manager.get_all_tools()),
        )

        # Handoff Protocol — standardized multi-agent task handoff
        from huanxin.handoff import HandoffProtocol
        self._handoff: HandoffProtocol = HandoffProtocol(
            audit_logger=self._audit_logger,
        )

        # Reflexion Engine — self-reflection & auto-correction
        from huanxin.reflexion import ReflexionEngine
        self._reflexion_engine: ReflexionEngine = ReflexionEngine(
            threshold=0.6,
            max_retries=3,
        )

        # State Machine — LangGraph-inspired execution engine
        from huanxin.state_machine import create_dispatch_workflow
        self._state_machine = create_dispatch_workflow()
        self._state_machine_data: dict = {}

        # RBAC Engine — role-based access control for enterprise security
        from huanxin.rbac import RBACEngine
        self._rbac_engine: RBACEngine = RBACEngine()

        # Context compression engine — manages long conversation histories
        from huanxin.context_compressor import ContextCompressor
        self._context_compressor: ContextCompressor = ContextCompressor(keep_recent=4)
        self._message_history: list[dict] = []  # Accumulated conversation context

        # Post-LLM Hallucination Guard — detects unverifiable claims in LLM output
        from huanxin.hallucination_guard import HallucinationGuard, GuardMode
        self._hallucination_guard: HallucinationGuard = HallucinationGuard(
            mode=GuardMode.STRICT,
            enable_llm_verification=False,
            max_correction_rounds=3,
        )

        # Guardrail Telemetry — OTel-style observability for guardrail events
        from huanxin.guardrail_telemetry import guardrail_telemetry
        self._guardrail_telemetry = guardrail_telemetry

        # P0.4 Agent Loop Boundedness — prevent unbounded loops from burning quota
        from huanxin.loop_guard import AgentLoopGuard
        self._loop_guard: AgentLoopGuard = AgentLoopGuard(
            max_iterations=20,
            max_cost_per_run=5.00,
            cost_tracker=self._cost_tracker,
        )

        # Three-tier tool guardrail — classify → risk → role-scoped access
        from huanxin.tool_guard import ThreeTierGuardEnhancement
        self._tool_guard: ThreeTierGuardEnhancement = ThreeTierGuardEnhancement()

        # Bounded autonomy — GREEN / YELLOW / RED action zones.
        # No approval_engine is injected on purpose: this instance is used by
        # the observation chain, and creating HITL approval requests as a
        # side-effect of a *check* would change business flow.  The real HITL
        # gate stays where it is, at the top of execute_task().
        from huanxin.bounded_autonomy import BoundedAutonomyEngine
        self._bounded_autonomy: BoundedAutonomyEngine = BoundedAutonomyEngine(
            approval_engine=None,
        )

        # P0.2 Guardrail chain — wires tool/loop/bounded-autonomy/hallucination
        # guards into the main execution path.  Shadow mode by default.
        from huanxin.guardrail_chain import GuardrailChain
        self._guardrail_chain: GuardrailChain = GuardrailChain(
            tool_guard=self._tool_guard,
            loop_guard=self._loop_guard,
            bounded_autonomy=self._bounded_autonomy,
            hallucination_guard=self._hallucination_guard,
            telemetry=self._guardrail_telemetry,
        )
        logger.info(
            "[Huanxin] Guardrail chain armed — mode=%s",
            self._guardrail_chain.mode.value,
        )

        # P3.10 Task Router — intent-based multi-level routing
        from huanxin.router import RouterEngine, IntentClassifier
        self._task_router: RouterEngine = RouterEngine(
            classifier=IntentClassifier(
                llm_engine=getattr(self, '_llm_engine', None),
            ),
        )

        # P3.11 Workflow Engine — DAG-based multi-step orchestration
        from huanxin.workflow import WorkflowEngine
        self._workflow_engine: WorkflowEngine = WorkflowEngine(
            name="emperor_default",
        )

        self._dispatch(LifecycleEvent.ON_INIT, emperor=self)

        # Load persisted state if data_dir set
        if self.config.data_dir:
            self._load_state()

        logger.info("[Huanxin] initialized — %d ministers",
                    len(self._court.active_ministers))

    # ── Court proxy ────────────────────────────────────────────────

    @property
    def court(self):
        """Direct access to the underlying Court."""
        return self._court

    @property
    def app_config(self) -> Optional[AppConfig]:
        """Access to the loaded huanxin.yaml config (if available)."""
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
    def smart_router(self):
        """Direct access to the P2.9 SmartRouter (capability-aware routing + fallback chains)."""
        return self._smart_router

    @property
    def cost_per_success(self):
        """Direct access to the CostPerSuccessTracker."""
        return self._cost_per_success

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
    def tool_guard(self):
        """Direct access to the three-tier ToolGuard (classify → risk → role)."""
        return self._tool_guard

    @property
    def bounded_autonomy(self):
        """Direct access to the BoundedAutonomyEngine (GREEN/YELLOW/RED zones)."""
        return self._bounded_autonomy

    @property
    def guardrail_chain(self):
        """Direct access to the GuardrailChain wired into the execution path."""
        return self._guardrail_chain

    @property
    def guardrail_telemetry(self):
        """Direct access to the GuardrailTelemetry (guardrail observability)."""
        return self._guardrail_telemetry

    @property
    def loop_guard(self):
        """Direct access to the P0.4 AgentLoopGuard (loop boundedness protection)."""
        return self._loop_guard

    @property
    def task_router(self):
        """Direct access to the P3.10 TaskRouter (intent-based multi-level routing)."""
        return self._task_router

    def route_task(
        self,
        user_input: str,
        available_capabilities: Optional[list[str]] = None,
    ) -> dict:
        """Route a user input through the task router for diagnostic preview.

        Args:
            user_input:  Natural-language user request.
            available_capabilities:  Capabilities to consider for routing.

        Returns:
            Dict with routing decision details.
        """
        ministers = self._court.active_ministers
        minister_names = [m.name for m in ministers]
        capabilities = available_capabilities or self._capability_registry.list_capabilities()
        decision = self._task_router.route(user_input, minister_names, capabilities)
        return {
            "target_type": decision.target_type,
            "target_name": decision.target_name,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
            "intent": decision.intent,
            "intent_confidence": decision.intent_confidence,
            "suggested_minister": decision.suggested_minister,
            "matched_capability": decision.matched_capability,
        }

    @property
    def route_stats(self) -> dict:
        """Return aggregated routing statistics."""
        return self._task_router.stats()

    @property
    def workflow_engine(self):
        """Direct access to the WorkflowEngine for DAG-based orchestration."""
        return self._workflow_engine

    @property
    def llm_engine(self):
        """Lazy-loaded multi-backend LLM manager (OpenAI / Anthropic / Ollama / free providers).

        Uses :func:`huanxin.llm.build_manager_from_env` so the emperor entry honours
        the same OPENAI_* env contract and failover chain as the domains main chain
        (``huanxin.core.llm.LLMManager``). Falls back to a single default backend if
        the env builder fails for any reason.
        """
        if not hasattr(self, '_llm_engine'):
            from huanxin.llm import LLMManager, LLMConfig, ModelProvider, build_manager_from_env
            try:
                self._llm_engine = build_manager_from_env()
            except Exception as e:  # noqa: BLE001 - keep emperor importable regardless
                logger.warning("[Huanxin] LLM manager env build failed (%s); using default backend", e)
                self._llm_engine = LLMManager(backends=[LLMConfig(
                    provider=ModelProvider.OPENAI,
                    model_name="gpt-4o",
                )])
            self._llm_config = self._llm_engine.config
        return self._llm_engine

    @property
    def llm_config(self):
        """Access the current LLMConfig (lazy-initialized with llm_engine)."""
        _ = self.llm_engine  # trigger lazy init
        return self._llm_config

    @property
    def message_history(self) -> list[dict]:
        """Current accumulated conversation context."""
        return self._message_history

    def clear_context(self) -> None:
        """Reset the accumulated conversation context."""
        self._message_history = []
        logger.info("[Huanxin] context history cleared")

    def _dispatch(self, event: Any, **kwargs: Any) -> Any:
        """Dispatch a lifecycle event to all registered plugins."""
        return self._plugin_manager.dispatch(event, **kwargs)

    def register(self, name: str, domain: str = "general",
                 temperature: float = 0.7) -> None:
        """Register a new minister."""
        from huanxin.plugin import LifecycleEvent

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
        from huanxin.plugin import LifecycleEvent

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
        # ── 学习曲线埋点：每轮进化后记录一个时序点（跨重启持久化）──
        try:
            from huanxin.learning_curve import record_evolve_round
            record_evolve_round(self._court)
        except Exception:  # 度量失败绝不拖垮进化主流程
            logger.debug("[Huanxin] learning-curve record skipped", exc_info=True)
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
        from huanxin.court.task_engine import TaskRequest
        from huanxin.plugin import LifecycleEvent
        from huanxin.guardrail_telemetry import (
            GuardrailEvent, GuardrailType, EventAction,
        )

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
                    "[Huanxin] RBAC denied: minister=%s role=%s permission=%s",
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

            # ── P0.4 Agent Loop Boundedness: iteration guard ──
            self._loop_guard.check_iteration(task_id)

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
                from huanxin.context_compressor import (
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
                        "[Huanxin] context compressed: %d→%d tokens (strategy=%s)",
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

            # ── Pre-LLM Prompt Injection Guard (P0.1) ──
            # Scan the user prompt before it reaches the LLM through TaskEngine.
            #
            # Two bugs were fixed here:
            #   1. PromptGuard() defaults to severity_threshold="warn", which
            #      downgrades every `dangerous` verdict to `suspicious` — so the
            #      blocking branch below could never fire.  We now request
            #      "block" semantics explicitly (override via
            #      HUANXIN_PROMPT_GUARD_MODE for a staged rollout).
            #   2. The dangerous branch only logged a warning and fell through
            #      to the LLM, while telemetry recorded action="blocked".  That
            #      is a guardrail that lies.  It now really aborts the task.
            import time as _pg_time
            _pg_t0 = _pg_time.perf_counter_ns()
            _pg_result = None
            _pg_available = True
            try:
                from huanxin.prompt_guard import PromptGuard
                _pg_mode = os.environ.get("HUANXIN_PROMPT_GUARD_MODE", "block")
                _pg = PromptGuard(severity_threshold=_pg_mode)
                _pg_result = _pg.scan_input(prompt)
            except Exception:
                _pg_available = False
                logger.error(
                    "[Huanxin] PromptInjectionGuard UNAVAILABLE for task=%s — "
                    "prompt reached the LLM unscreened",
                    task_id, exc_info=True,
                )

            if _pg_available and _pg_result is not None:
                _pg_latency_us = (_pg_time.perf_counter_ns() - _pg_t0) // 1000
                _pg_blocked = _pg_result.level == "dangerous"
                self._guardrail_telemetry.emit(GuardrailEvent(
                    guardrail_type=GuardrailType.PRE_LLM,
                    trigger_rule=_pg_result.matched_rules,
                    severity=_pg_result.level,
                    action=EventAction.BLOCKED if _pg_blocked else EventAction.ALLOWED,
                    input_snippet=prompt[:200],
                    latency_us=_pg_latency_us,
                ))
                if _pg_blocked:
                    logger.warning(
                        "[Huanxin] PromptInjectionGuard BLOCKED task=%s "
                        "level=%s rules=%s",
                        task_id, _pg_result.level, _pg_result.matched_rules,
                    )
                    _trace_status = "ok"
                    _trace_attrs = {
                        "status": "blocked",
                        "guard": "prompt_guard",
                        "rules": ",".join(_pg_result.matched_rules),
                    }
                    self._dispatch(
                        LifecycleEvent.ON_TASK_AFTER,
                        task_id=task_id, success=False, blocked=True,
                    )
                    return {
                        "task_id": task_id,
                        "status": "blocked",
                        "minister": "__guard__",
                        "success": False,
                        "confidence": 0.0,
                        "merit_score": 0.0,
                        "execution_time_ms": 0.0,
                        "response": "",
                        "error": (
                            "prompt_injection_blocked:rules="
                            f"{','.join(_pg_result.matched_rules)}"
                        ),
                        "handoff": None,
                        "guard": {
                            "name": "prompt_guard",
                            "level": _pg_result.level,
                            "matched_rules": list(_pg_result.matched_rules),
                            "confidence": _pg_result.confidence,
                            "reason": _pg_result.reason,
                        },
                    }

            # ── P0.2 Guardrail chain (tool / loop / bounded autonomy) ──
            # Shadow mode by default: every guard runs and emits telemetry,
            # nothing is blocked.  HUANXIN_GUARDRAIL_MODE=enforce turns a
            # `dangerous` verdict into a real stop.
            _chain_pre = self._guardrail_chain.run_pre_execution(
                task_id=task_id, prompt=prompt, domain=domain,
            )
            if _chain_pre.blocked:
                _trace_status = "ok"
                _trace_attrs = {
                    "status": "blocked",
                    "guard": (
                        _chain_pre.blocking_check.guard
                        if _chain_pre.blocking_check else "guardrail"
                    ),
                }
                self._dispatch(
                    LifecycleEvent.ON_TASK_AFTER,
                    task_id=task_id, success=False, blocked=True,
                )
                return self._guardrail_chain.blocked_payload(_chain_pre, task_id)

            # ── P2.9 Smart Routing: capability classification ──
            _smart_cap: str = "unknown"
            _smart_tier: str = "standard"
            _smart_chain: list[str] = []
            try:
                _cap = self._smart_router.classify(prompt, domain)
                _smart_cap = _cap.value
                _smart_tier = self._smart_router.get_tier_for_capability(_cap)
                _smart_chain = self._smart_router.get_fallback_chain_for_tier(_smart_tier)
                logger.debug(
                    "[Huanxin] SmartRouter: cap=%s tier=%s chain=%s",
                    _smart_cap, _smart_tier, _smart_chain,
                )
            except Exception:
                logger.debug("[Huanxin] SmartRouter classification unavailable", exc_info=True)

            # ── P3.10 Task Router: intent classification & minister routing ──
            _route_decision = None
            try:
                ministers = self._court.active_ministers
                minister_names = [m.name for m in ministers]
                caps = self._capability_registry.list_capabilities()
                _route_decision = self._task_router.route(
                    prompt, minister_names, caps,
                )
                logger.debug(
                    "[Huanxin] TaskRouter: intent=%s minister=%s cap=%s conf=%.2f",
                    _route_decision.intent,
                    _route_decision.target_name,
                    _route_decision.matched_capability or "-",
                    _route_decision.confidence,
                )
            except Exception:
                logger.debug("[Huanxin] TaskRouter unavailable", exc_info=True)

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
                "route_intent": _route_decision.intent if _route_decision else None,
                "route_target": _route_decision.target_name if _route_decision else None,
                "route_confidence": _route_decision.confidence if _route_decision else None,
            }

            # ── Post-execution handoff check ──
            # If the TaskRequest meta contains a handoff target and the minister
            # indicated a handoff is needed, execute the handoff protocol.
            if req.meta and req.meta.get("handoff_target"):
                from huanxin.handoff import (
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
                    "[Huanxin] Handoff %s: %s → %s (%s)",
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
                    logger.warning("[Huanxin] Failed to persist task to DB")

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
                        logger.info("[Huanxin] Reflexion corrected task=%s conf=%.4f", task_id, refl.confidence)
                    elif refl.status.value == "failed":
                        logger.warning("[Huanxin] Reflexion failed for task=%s after %d attempts", task_id, refl.attempts)
                except Exception:
                    logger.exception("[Huanxin] Reflexion error for task=%s", task_id)

            # ── Post-LLM guardrail chain (P0.2) ──
            # Runs HallucinationGuard through the same shadow/enforce chain as
            # the pre-LLM guards, so telemetry is emitted 1:1 with invocations
            # and the `action` field always matches what actually happened.
            if result["success"] and result.get("response"):
                _chain_post = self._guardrail_chain.run_post_execution(
                    task_id=task_id,
                    response=str(result["response"]),
                    prompt=prompt,
                    domain=domain,
                )
                result["guardrail"] = _chain_post.to_dict()
                for _check in _chain_post.checks:
                    if _check.guard == "hallucination_guard" and _check.payload:
                        result["hallucination_guard"] = _check.payload
                        if _check.payload.get("has_hallucinations"):
                            logger.warning(
                                "[Huanxin] HallucinationGuard flagged %d claims in "
                                "task=%s (confidence=%.4f)",
                                _check.payload.get("flagged_sentences", 0),
                                task_id,
                                _check.payload.get("confidence", 0.0),
                            )
                if _chain_post.blocked:
                    result["success"] = False
                    result["error"] = (
                        "guardrail_blocked:guard="
                        f"{_chain_post.blocking_check.guard}"
                        if _chain_post.blocking_check else "guardrail_blocked"
                    )
                    result["response"] = ""

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

            # ── Cost-per-successful-run tracking ──
            # Gather cost data for this task from the cost tracker
            _task_cost = 0.0
            _task_tokens_in = 0
            _task_tokens_out = 0
            _model_calls = 0
            try:
                for r in self._cost_tracker._records_snapshot():
                    if r.task_id == task_id:
                        _task_cost += r.cost_usd
                        _task_tokens_in += r.tokens_in
                        _task_tokens_out += r.tokens_out
                        _model_calls += 1
            except Exception:
                logger.debug(
                    "[Huanxin] 成本快照不可用（成本追踪快照读取失败，已跳过，不阻断主流程）",
                    exc_info=True,
                )
            try:
                self._cost_per_success.record(
                    task_id=task_id,
                    success=result["success"],
                    cost_usd=_task_cost,
                    tokens_in=_task_tokens_in,
                    tokens_out=_task_tokens_out,
                    execution_time_ms=_elapsed_ms,
                    domain=domain,
                    model_calls=_model_calls,
                )
            except Exception:
                logger.debug("[Huanxin] CostPerSuccessTracker unavailable", exc_info=True)

            # ── P0.4 Agent Loop Boundedness: cost cap + loop detection ──
            try:
                self._loop_guard.check_cost(task_id, _task_cost)
                # Record action for dead-loop detection using the outcome
                _action_label = f"execute_task:{domain}"
                _result_sig = str(result.get("response", ""))[:500]
                self._loop_guard.record_action(task_id, _action_label, _result_sig)
            except Exception:
                logger.warning(
                    "[Huanxin] loop guard 检查失败，已跳过（静默失效已消除）",
                    exc_info=True,
                )

            return result

        finally:
            _tracer.end_span(_trace_ctx.span_id, status=_trace_status, attributes=_trace_attrs)

    # ── Consensus deliberation ─────────────────────────────────────

    def deliberate(
        self,
        text: str,
        *,
        ministers: Optional[list[str]] = None,
        strategy: Optional[Any] = None,
        num_ministers: int = 3,
        critique_rounds: int = 1,
    ) -> Any:
        """Run multi-minister deliberation to form a consensus answer.

        Each selected minister independently processes the task,
        then cross-critiques each other's outputs, and the chosen
        strategy synthesizes a final consensus answer.

        Args:
            text: The task/question text to deliberate on.
            ministers: Optional list of minister names. If None,
                the top ``num_ministers`` from the court are auto-selected.
            strategy: ConsensusStrategy to use. One of:
                - MajorityVote (default)
                - WeightedVote (minister merit-weighted)
                - DebateRound (multi-round debate)
                - BestOfN (highest-confidence answer)
                - SynthesisConsensus (LLM synthesis)
                Defaults to MajorityVote if not provided.
            num_ministers: How many ministers to involve. Default 3.
            critique_rounds: Cross-critique rounds. Default 1.

        Returns:
            ConsensusResult with ``final_answer``, ``confidence``,
            ``votes``, ``scores``, ``critiques``, etc.

        Example::

            from huanxin.consensus import WeightedVote

            result = emperor.deliberate(
                "Should we refactor the auth module?",
                strategy=WeightedVote(),
                num_ministers=5,
            )
            print(result.final_answer)
        """
        from huanxin.consensus import ConsensusEngine, MajorityVote
        from huanxin.consensus.engine import ConsensusConfig
        from huanxin.consensus.strategies import MinisterOutput

        if ministers is None:
            ministers = self._court.active_ministers[:num_ministers]
            if len(ministers) < 2:
                # Fall back to default ministers if none are active
                ministers = [t[0] for t in self.DEFAULT_MINISTERS[:num_ministers]]

        config = ConsensusConfig(
            num_ministers=num_ministers,
            critique_rounds=critique_rounds,
            require_critique=critique_rounds > 0,
            strategy=strategy or MajorityVote(),
        )

        engine = ConsensusEngine(court=self._court, config=config)

        def _executor(minister_name: str, task: str) -> MinisterOutput:
            """Bridge: convert Huanxin's execute_task output to MinisterOutput."""
            result = self.execute_task(task, domain="general")
            return MinisterOutput(
                minister=minister_name,
                answer=str(result.get("response", "")),
                reasoning=str(result.get("reasoning", "")),
                confidence=float(result.get("confidence", 0.75)),
                merit_score=float(result.get("merit_score", 50.0)),
            )

        return engine.deliberate(
            text,
            ministers=ministers,
            executor=_executor,
            strategy=strategy,
        )

    # ── Workflow execution ─────────────────────────────────────────

    def execute_workflow(
        self,
        workflow_def: Union[str, dict, Path],
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute a DAG-based workflow.

        Args:
            workflow_def: A YAML/JSON file path, or a dict definition.
            context: Optional initial context dict shared across all nodes.

        Returns:
            Dict with ``results``, ``statuses``, ``errors``,
            ``execution_order``, and ``success``.

        Example::

            emperor.execute_workflow({
                "name": "data_pipeline",
                "nodes": [
                    {"type": "TaskNode", "node_id": "A", "label": "Fetch"},
                    {"type": "TaskNode", "node_id": "B", "label": "Transform"},
                    {"type": "TaskNode", "node_id": "C", "label": "Save"},
                ],
                "edges": [["A", "B"], ["B", "C"]],
            })
        """
        from huanxin.workflow import WorkflowEngine

        if isinstance(workflow_def, (str, Path)):
            path = Path(workflow_def)
            if not path.exists():
                raise FileNotFoundError(f"Workflow file not found: {path}")

            if path.suffix.lower() in (".yaml", ".yml"):
                engine = WorkflowEngine.from_yaml(path)
            elif path.suffix.lower() == ".json":
                engine = WorkflowEngine.from_json(path)
            else:
                raise ValueError(
                    f"Unsupported workflow file format: {path.suffix}"
                )
        elif isinstance(workflow_def, dict):
            engine = WorkflowEngine.from_dict(workflow_def)
        else:
            raise TypeError(
                f"workflow_def must be str/Path/dict, got {type(workflow_def)}"
            )

        # Update the shared engine with the emperor's context
        if context:
            engine._base_context.update(context)

        logger.info(
            "[Huanxin] executing workflow '%s' (%d nodes)",
            engine.name, engine.dag.node_count,
        )
        return engine.run()

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

        # 启动即确保大臣基因多样化并落盘：
        #  - 已有 genomes.json 的旧代码坍缩（同质基因 → 永久 [Diversity]
        #    Crisis）在 _load_state 中已自愈；此处兜底覆盖 _load_state 未跑
        #    的路径（如无 data_dir 时的内存态）。
        #  - 全新部署则把 archetype 播种的多样化大臣立即持久化，避免被
        #    SIGKILL 吞掉而未落盘。
        try:
            _court_dir = Path(
                getattr(self.config, "court_path", "")
                or getattr(self.config, "data_dir", "")
                or "."
            ).resolve()
            _gp = (
                Path(self._court._sm._genome_path)
                if self._court._sm._genome_path
                else (_court_dir / "genomes.json")
            )
            if self._court.redisperse_if_homogeneous() or not _gp.exists():
                self._court.save_genomes(str(_gp))
        except Exception:
            logger.warning(
                "[Huanxin] 启动基因自检/落盘失败（已忽略）", exc_info=True
            )

        # ── Initialize database persistence ────────────────────────
        import os
        from huanxin.database import Database

        # court_path（HUANXIN_COURT_PATH / HUANXIN_DATA_DIR）优先，
        # 未配置时沿用 CWD（历史行为）。容器里指向挂载卷 /app/data。
        court_dir = getattr(self.config, "court_path", "") or os.getcwd()
        ensure_dir(court_dir)
        db_path = os.path.join(court_dir, "huanxin.db")
        logger.info("[Huanxin] 法庭主库 → %s", db_path)
        db = Database(db_path)
        self._court.db = db

        # Inject db into alert manager and scheduler for persistence
        self.alerts._db = db
        if self._scheduler is not None:
            self._scheduler._db = db

        if self.config.auto_schedule:
            self._auto_start_scheduler()

        from huanxin.court_api import create_app, configure_app

        app = create_app(court=self._court, eval_runner=self._eval_runner, audit_logger=self._audit_logger, template_manager=self._template_manager)
        configure_app(self.app_config)
        app.extra["host"] = host
        app.extra["port"] = port
        app.extra["emperor"] = self
        app.extra["db"] = db
        app.extra["model_router"] = self._model_router
        app.extra["multi_model_router"] = self._multi_model_router
        app.extra["cost_tracker"] = self._cost_tracker
        app.extra["cost_per_success"] = self._cost_per_success
        app.extra["smart_router"] = self._smart_router
        app.extra["graph_rag"] = self._graph_rag
        app.extra["guardrail_telemetry"] = self._guardrail_telemetry

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

        logger.info("[Huanxin] API + Dashboard → http://%s:%d", host, port)
        logger.info("[Huanxin] Dashboard → http://%s:%d/dashboard", host, port)
        if self._scheduler is not None and self._scheduler.state.name == "RUNNING":
            r = self._scheduler.report()
            logger.info(
                "[Huanxin] Scheduler RUNNING — %d jobs, evolution every %s min, tasks every %s min",
                len(r.entries),
                self.config.auto_evolve_interval_minutes,
                self.config.auto_tasks_interval_minutes,
            )
        uvicorn.run(app, host=host, port=port)

    # ── One-command live dashboard helpers ────────────────────────

    # Default minister lineup — used when huanxin.yaml is absent.
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

        Returns tuples from huanxin.yaml if present, otherwise falls back
        to _FALLBACK_MINISTERS.
        """
        if self.app_config is not None and self.app_config.seed_ministers:
            return [(m["name"], m["domain"]) for m in self.app_config.seed_ministers]
        return self._FALLBACK_MINISTERS

    def _ensure_default_ministers(self) -> int:
        """Auto-register a default minister lineup if the court is empty.

        Uses ``seed_ministers`` from ``huanxin.yaml`` if present, otherwise
        falls back to ``_FALLBACK_MINISTERS``.

        Returns:
            Number of new ministers actually registered (0 if court
            was already populated).
        """
        existing = set(self._court.active_ministers)

        # Honour huanxin.yaml seed_ministers
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
                    logger.warning("[Huanxin] seed register %s failed: %s", name, e)
        if seeded:
            logger.info("[Huanxin] auto-seeded %d ministers: %s",
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
            {"prompt": "把 'Hello 幻炘AI' 反转并统计字符数", "domain": "general"},     # → text
            {"prompt": "查看 huanxin/emperor.py 文件的行数和文件大小", "domain": "code"},      # → file_info
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
            "[Huanxin] auto-scheduler started: evolve every %.1f min, tasks every %.1f min",
            self.config.auto_evolve_interval_minutes,
            self.config.auto_tasks_interval_minutes,
        )

        # ── Immediate first run so dashboard shows live data on boot ──
        try:
            logger.info("[Huanxin] running first evolution (%d cycles) …",
                        self.config.auto_evolve_cycles)
            self.evolve(cycles=self.config.auto_evolve_cycles)
        except Exception:
            logger.exception("[Huanxin] first evolution failed")

        try:
            logger.info("[Huanxin] running first task batch (%d tasks) …",
                        len(task_templates))
            self.execute_batch(task_templates)
        except Exception:
            logger.exception("[Huanxin] first task batch failed")

        return True

    @property
    def app(self):
        """Lazy-loaded FastAPI app (for testing)."""
        if self._app is None:
            from huanxin.court_api import create_app
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
            "  Huanxin Evolution Dashboard",
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
            # 钉住基因文件路径，使随后的 save_genomes()（自愈 / 优雅关闭）
            # 写回同一个文件，而非因 _genome_path 为空而静默丢弃。
            self._court._sm._genome_path = str(genomes_file)
            # 重启自愈：若已部署的大臣基因同质（旧代码的 groupthink 坍缩，
            # 表现为永久 [Diversity] Crisis similarity=1.000），立即按
            # archetype 再散布并落盘，使本轮 serve 在多样化种群上进化。
            try:
                if self._court.redisperse_if_homogeneous():
                    self._court.save_genomes()
                    logger.warning(
                        "[Huanxin] 启动检测到同质大臣基因，已自愈并落盘 %s",
                        genomes_file,
                    )
            except Exception:
                logger.warning(
                    "[Huanxin] 启动自愈失败（已忽略，不影响启动）",
                    exc_info=True,
                )
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
        logger.info("[Huanxin] state saved → %s", target)
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

        logger.info("[Huanxin] state loaded from %s", target)

    # ── Shutdown ───────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Graceful shutdown — stop scheduler, save state, clean up."""
        from huanxin.plugin import LifecycleEvent

        self._dispatch(LifecycleEvent.ON_SHUTDOWN, emperor=self)
        if self._scheduler is not None:
            self._scheduler.stop()
        if self.config.data_dir:
            self.save()
        logger.info("[Huanxin] shutdown complete")

    # ── Scheduler ──────────────────────────────────────────────────

    @property
    def scheduler(self):
        """Lazy-loaded scheduler for periodic automation."""
        if self._scheduler is None:
            from huanxin.court.scheduler import Scheduler
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
            from huanxin.alerts import AlertManager
            self._alert_manager = AlertManager()
        return self._alert_manager

    @property
    def metrics(self):
        """MetricsPlugin for performance telemetry (auto-registered on init)."""
        if self._metrics_plugin is None:
            # Should never happen — registered in __init__
            from huanxin.plugins import MetricsPlugin
            self._metrics_plugin = MetricsPlugin()
            self._plugin_manager.register(self._metrics_plugin)
        return self._metrics_plugin

    @property
    def healing(self):
        """Lazy-loaded HealingEngine for automatic recovery."""
        if self._healing_engine is None:
            from huanxin.healing import HealingEngine
            self._healing_engine = HealingEngine()
            self._register_default_healing_actions()
        return self._healing_engine

    def pipeline_monitor(self):
        """Lazy-loaded PipelineMonitor for real-time DAG visualization."""
        if self._pipeline_monitor is None:
            from huanxin.pipeline_monitor import PipelineMonitor
            from huanxin.pipeline import pipeline_registry
            self._pipeline_monitor = PipelineMonitor()
            self._pipeline_monitor.attach(pipeline_registry)
            logger.info("[Huanxin] Pipeline monitor attached")
        return self._pipeline_monitor

    def _register_default_healing_actions(self) -> None:
        """Register pre‑baked healing actions on first access."""
        from huanxin.healing import HealingAction
        from huanxin.healing_actions import (
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
        logger.info("[Huanxin] Registered %d default healing actions",
                    len(engine.list_actions()))
