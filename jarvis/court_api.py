"""FastAPI REST API server for the Court evolutionary system.

Usage:
    python -m jarvis.court_api                       # default: 127.0.0.1:8000
    python -m jarvis.court_api --port 9000            # custom port
    python -m jarvis.court_api --config court.yaml    # config-driven

Endpoints:
    GET  /                        — server health
    GET  /health                  — 轻量存活探针（容器/云平台健康检查）
    GET  /court/summary           — court summary
    GET  /court/snapshot          — structured court state
    GET  /court/history           — evolution cycle history
    GET  /court/ministers         — list all ministers
    GET  /court/minister/{name}   — detail for one minister
    POST /court/register          — register a minister
    POST /court/register/batch    — bulk register
    POST /court/evolve            — run N evolution cycles
    POST /court/dispatch          — record a dispatch outcome
    POST /court/feedback          — record external feedback
    POST /court/genomes/save      — persist genomes
    POST /court/genomes/load      — load genomes from file
    POST /court/config/load       — load config from YAML
    GET  /court/config            — view current config
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("jarvis.court_api")

from jarvis.court.config import SurvivalConfig
from jarvis.court.court import Court

# Sandbox imports
from jarvis.sandbox import SandboxManager

# P0 module imports for API endpoints
from jarvis.governance_agent import GovernanceAgent, GovernanceRule, RulePriority
from jarvis.bounded_autonomy import ActionZone, ActionSpace, BoundedAutonomyEngine

# P1 module imports for API endpoints
from jarvis.tool_guard import (
    ToolGuardMiddleware, InputValidator, RateLimiter, OutputFilter,
    GuardEvent, GuardEventType, GuardResult,
)
from jarvis.hallucination_detector import (
    HallucinationDetector, HallucinationResult, RiskLevel,
)
from jarvis.hierarchical_memory import (
    HierarchicalMemoryEngine, MemoryTier, ConsolidationStatus,
)

# P1 module imports for Prompt Injection Guard
from jarvis.prompt_guard import PromptGuard, ScanResult

# RBAC module imports
from jarvis.rbac import RBACEngine, Permission, Role, intent_to_permission

# 可选启用的 Token 鉴权中间件（EMPEROR_API_TOKEN 未设则不生效）
from jarvis.api.token_guard import add_token_auth
# 多用户 / 会话 / token 用量存储层
from jarvis.api import auth_store
# 鉴权依赖（get_current_user / require_admin，提炼自本模块闭包，供单测与复用）
from jarvis.api.deps import get_current_user, require_admin
# 能力服务：文件上传 / 联网搜索 / 图文识别
from jarvis.capabilities import UploadStore, WebSearchService, build_vision_processor


# ══════════════════════════════════════════════════════════════════
# Request models (module-level for FastAPI type resolution)
# ══════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    name: Optional[str] = None
    domain: str = "general"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    confidence_baseline: float = Field(default=0.75, ge=0.0, le=1.0)


class BulkRegisterRequest(BaseModel):
    ministers: list[RegisterRequest]


class EvolveRequest(BaseModel):
    cycles: int = Field(default=1, ge=1, le=100)


class EvolutionRunRequest(BaseModel):
    cycles: int = Field(default=3, ge=1, le=200, description="运行的进化轮数")


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息")
    history: list[dict] = Field(default_factory=list, description="对话历史 [{role, content}]（前端内存态，兼容未建会话时）")
    conversation_id: Optional[int] = Field(default=None, description="会话 ID；提供则从数据库加载持久化历史并保存消息")
    system: str = Field(
        default="你是 Emperor Core —— 一个会自我进化的 AI 助手，回答简洁、准确、有帮助。",
        description="系统提示词",
    )
    web_search: bool = Field(default=False, description="是否开启联网搜索")
    image_url: Optional[str] = Field(default=None, description="图片 URL（视觉识别）")
    file_id: Optional[str] = Field(default=None, description="已上传文件引用（图片→视觉；pdf/txt/md→抽取文本）")


class AuthRequest(BaseModel):
    username: str
    password: str


class VisionRequest(BaseModel):
    image_url: Optional[str] = None
    file_id: Optional[str] = None
    prompt: str = Field(default="Describe this image in detail.", description="视觉提问提示词")


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=10)


class AdminSetBannedRequest(BaseModel):
    banned: bool = True


class AdminResetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=6)


class AdminSetQuotaRequest(BaseModel):
    quota: Optional[dict] = None  # None = 不限额


class ConversationCreate(BaseModel):
    title: str = "新对话"


class ConversationRename(BaseModel):
    title: str


class DispatchRequest(BaseModel):
    minister: str
    edict_id: str
    intent: str
    success: bool
    confidence: float = Field(ge=0.0, le=1.0)
    execution_time_ms: float = 0.0


class FeedbackRequest(BaseModel):
    minister: str
    edict_id: str
    score: float = Field(ge=0.0, le=1.0)


class GenomeLoadRequest(BaseModel):
    path: str


class ConfigLoadRequest(BaseModel):
    path: str


class DashboardExecuteRequest(BaseModel):
    prompt: str
    domain: Optional[str] = None


class ManualTaskRequest(BaseModel):
    prompt: str
    domain: str = "general"


class MinisterCreateRequest(BaseModel):
    name: str
    domain: str = "general"


class MinisterUpdateRequest(BaseModel):
    domain: Optional[str] = None
    merit: Optional[float] = None
    stability: Optional[float] = None


class SchedulerConfigRequest(BaseModel):
    evolve_interval_minutes: Optional[float] = Field(default=None, ge=1, le=1440)
    task_interval_minutes: Optional[float] = Field(default=None, ge=1, le=1440)
    auto_schedule: Optional[bool] = None


class ThemeRequest(BaseModel):
    theme: str = "dark"


class TemplateOptimizeRequest(BaseModel):
    capability: str = Field(..., description="Capability name to optimize")


class TemplateFeedbackRequest(BaseModel):
    capability: str = Field(..., description="Capability name")
    score: float = Field(..., ge=0.0, le=1.0, description="Feedback score 0.0~1.0")


class TemplateRollbackRequest(BaseModel):
    capability: str = Field(..., description="Capability name")
    version: int = Field(..., ge=1, description="Target version to rollback to")


# ── Sandbox request models (module-level for FastAPI resolution) ─

class SandboxRunRequest(BaseModel):
    code: str = Field(..., description="Python code to execute")
    engine: str = Field(default="local_subprocess", description="Sandbox engine: local_subprocess | local_direct")
    timeout: int = Field(default=30, ge=1, le=300, description="Timeout in seconds (1-300)")


class SandboxShellRequest(BaseModel):
    command: str = Field(..., description="Shell command to execute")
    timeout: int = Field(default=30, ge=1, le=300)


class ApprovalActionRequest(BaseModel):
    note: str = ""


class ApprovalPolicyRequest(BaseModel):
    rule_type: str = Field(..., description="domain | risk_level | capability | keyword")
    rule_value: str = Field(..., description="Matching value")
    enabled: bool = True


class HealingToggleRequest(BaseModel):
    enabled: bool = True


class MemoryAddRequest(BaseModel):
    content: str = Field(..., description="Memory content")
    tier: str = Field(default="working", description="Memory tier: working | episodic | semantic | procedural")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="Importance score 0.0~1.0")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional metadata")


# ════════════════════════ Governance API Models ═══════════════════

class GovernanceRuleRequest(BaseModel):
    name: str = Field(..., description="Unique rule name")
    rule_type: str = Field(..., description="policy | rbac | regulatory | business_logic")
    description: str = ""
    priority: str = Field(default="MEDIUM", description="CRITICAL | HIGH | MEDIUM | LOW")
    check_logic: str = Field(..., description="Python lambda expression for the check function, e.g. 'lambda action, ctx: ...'")


class GovernanceToggleRequest(BaseModel):
    enabled: bool = True


class GovernanceValidateRequest(BaseModel):
    action: dict = Field(..., description="Action to validate, e.g. {'tool': 'read', 'prompt': 'list files'}")
    context: dict = Field(default_factory=dict, description="Optional context, e.g. {'domain': 'general'}")


# ── Dashboard Governance API Models ─────────────────────────────

class GovernanceCreateRequest(BaseModel):
    description: str = Field(..., description="Rule description")
    priority: str = Field(default="P2", description="P0 | P1 | P2 | P3")
    remediation: str = Field(default="", description="Optional remediation suggestion")


# ── Dashboard Alert Rules API Models ────────────────────────────

class AlertRuleCreateRequest(BaseModel):
    name: str = Field(..., description="Rule name")
    condition: str = Field(..., description="Trigger condition description")
    threshold: float = Field(..., description="Threshold value")
    severity: str = Field(default="warning", description="critical | warning | info")


# ════════════════════ Bounded Autonomy API Models ═════════════════

class AutonomySpaceRequest(BaseModel):
    name: str = Field(..., description="Unique space name")
    zone: str = Field(..., description="GREEN | YELLOW | RED")
    keywords: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    risk_levels: list[str] = Field(default_factory=list)
    priority: int = Field(default=100, ge=0, le=1000)
    description: str = ""


class AutonomyClassifyRequest(BaseModel):
    action: dict = Field(..., description="Action to classify, e.g. {'tool': 'read'}")
    context: dict = Field(default_factory=dict)


# ═══════════════════════ Failure Recovery API Models ══════════════

class CircuitBreakerResetRequest(BaseModel):
    pass  # no body needed for reset


# ══════════════════════ Tool Guard API Models ═════════════════════

class ValidateInputRequest(BaseModel):
    tool_name: str = Field(default="unknown", description="Tool name for context")
    input_data: dict = Field(..., description="Tool call input to validate")


class FilterOutputRequest(BaseModel):
    tool_name: str = Field(default="unknown", description="Tool name for context")
    output_data: str = Field(..., description="Raw output string to filter")


class RateLimitResetRequest(BaseModel):
    tool_name: str = Field(default="", description="Tool name to reset; empty = all")


# ══════════════════ Hallucination Detector API Models ═════════════

class HallucinationDetectRequest(BaseModel):
    output: str = Field(..., description="LLM-generated output text")
    context: dict = Field(default_factory=dict, description="Ground truth context")


class HallucinationMultiDetectRequest(BaseModel):
    outputs: list[str] = Field(..., min_length=2, max_length=5,
                                description="2-5 output samples from repeated LLM calls")
    context: dict = Field(default_factory=dict, description="Ground truth context")


# ══════════════════════ LLM Judge API Models ══════════════════════

class JudgeEvaluateRequest(BaseModel):
    output: str = Field(..., description="Agent output text to evaluate")
    expected: str = Field(default="", description="Expected / reference answer")
    criteria: list[str] = Field(
        default_factory=lambda: ["accuracy", "completeness", "relevance", "safety"],
        description="Judging dimensions: accuracy, completeness, relevance, safety",
    )


class JudgeCompareRequest(BaseModel):
    output_a: str = Field(..., description="First candidate output")
    output_b: str = Field(..., description="Second candidate output")
    expected: str = Field(default="", description="Expected / reference answer")
    criteria: list[str] = Field(
        default_factory=lambda: ["accuracy", "completeness", "relevance", "safety"],
        description="Judging dimensions: accuracy, completeness, relevance, safety",
    )


class WorkflowExecuteRequest(BaseModel):
    workflow_name: str = "dispatch_workflow"
    data: dict = {}
    max_loops: int = 3
    max_retries: int = 3


# ══════════════════════════════════════════════════════════════════
# Module-level scheduler state (shared with Emperor.serve)
# ══════════════════════════════════════════════════════════════════

_scheduler_config: dict = {
    "evolve_interval_minutes": 5.0,
    "task_interval_minutes": 3.0,
    "auto_schedule": True,
}

_emperor_config = None
"""Module-level reference to the EmperorConfig (jarvis.yaml AppConfig).
Injected by Emperor.serve() via configure_app()."""


def configure_app(emperor_config=None):
    """Inject jarvis.yaml AppConfig so API endpoints can read/write it.

    Args:
        emperor_config: An AppConfig instance from jarvis.yaml.
    """
    global _emperor_config
    if emperor_config is not None:
        _emperor_config = emperor_config


# ── Cached LLM manager (for the ChatGPT-style chat endpoint) ──
_llm_manager = None


def _get_llm_manager():
    """Lazily build (and cache) the multi-backend LLM manager from env.

    Uses the same env-driven resolution as the rest of the app, so NVIDIA NIM /
    DeepSeek / OpenAI keys injected into the container are picked up automatically.
    """
    global _llm_manager
    if _llm_manager is None:
        from jarvis.core.llm import build_manager_from_env
        _llm_manager = build_manager_from_env()
    return _llm_manager


# 图片扩展名（用于判断上传文件是否走视觉识别）
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _read_file_text(path: str, ext: str) -> str:
    """从已上传文件中抽取文本（txt/md 直读；pdf 走 PyPDF2）。

    失败返回可读占位文案而非抛异常（能力服务降级原则）。
    """
    limit = 8000
    try:
        if ext in (".txt", ".md"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()[:limit]
        if ext == ".pdf":
            import PyPDF2

            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages)[:limit]
    except Exception as e:  # noqa: BLE001
        logger.warning("文件文本抽取失败（%s）：%s", ext, e)
    return f"[文件文本抽取失败：{ext}]"


# ── Background evolution job state (non-blocking /api/evolution/run) ──
_evo_lock = threading.Lock()
_evo_job: dict = {
    "running": False,
    "rounds_total": 0,
    "rounds_done": 0,
    "started_at": None,
    "finished_at": None,
    "last_error": None,
    "last_recorded_round": 0,
}


# ══════════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════════


def _hallucination_result_dict(result: HallucinationResult) -> dict:
    """Convert HallucinationResult to JSON-safe dict for API responses."""
    return {
        "risk_score": result.risk_score,
        "risk_level": result.risk_level.value,
        "issues": result.issues,
        "suggested_action": result.suggested_action,
        "blocked": result.blocked,
    }


def create_app(
    config: SurvivalConfig | None = None,
    court: Court | None = None,
    eval_runner: Optional[Any] = None,
    audit_logger: Optional[Any] = None,
    template_manager: Optional[Any] = None,
    governance_agent: Optional[Any] = None,
    bounded_autonomy_engine: Optional[Any] = None,
    recovery_engine: Optional[Any] = None,
    hierarchical_memory_engine: Optional[HierarchicalMemoryEngine] = None,
) -> FastAPI:
    """Create a FastAPI app wired to a Court instance.

    Args:
        config: Optional SurvivalConfig to load.
        court: Optional pre-built Court instance to inject.
        eval_runner: Optional EvalRunner instance for /api/dashboard/evals endpoints.
        audit_logger: Optional AuditLogger instance for /api/dashboard/audit endpoints.
        template_manager: Optional PromptTemplateManager for adaptive prompt templates.
        governance_agent: Optional GovernanceAgent for /governance endpoints.
        bounded_autonomy_engine: Optional BoundedAutonomyEngine for /autonomy endpoints.
        recovery_engine: Optional RecoveryEngine for /recovery endpoints.
    """
    app = FastAPI(title="Emperor Court API", version="0.1.0")

    # 多用户存储层初始化（幂等建表；数据落在 $EMPEROR_DATA_DIR/emperor.db，数据卷持久化）
    auth_store.init_db()
    # 强制登录 + 单用户部署：启动时种入唯一管理员账号。
    # 凭据来源：EMPEROR_ADMIN_USER / EMPEROR_ADMIN_PASS；若未设则回退到
    # EMPEROR_API_TOKEN 的值作为初始密码（保证升级后不会锁死），再不行则随机生成并打印告警。
    admin_user = (os.getenv("EMPEROR_ADMIN_USER", "admin") or "admin").strip()
    admin_pass = (os.getenv("EMPEROR_ADMIN_PASS", "") or "").strip() or (os.getenv("EMPEROR_API_TOKEN", "") or "").strip()
    if not admin_pass:
        admin_pass = secrets.token_urlsafe(16)
        logger.warning(
            "EMPEROR_ADMIN_PASS 与 EMPEROR_API_TOKEN 均未设置，已生成随机管理员密码"
            "（仅本次启动有效，请尽快在 .env 固定 EMPEROR_ADMIN_PASS）：%s", admin_pass
        )
    auth_store.ensure_admin(admin_user, admin_pass)
    logger.info("已确保管理员账号存在：username=%s", admin_user)
    # 强制会话登录鉴权（详见 jarvis/api/token_guard.py）
    add_token_auth(
        app,
        session_validator=auth_store.is_session_valid,
        public_paths=(
            "/health",
            "/api/auth/login",
            "/api/auth/register",
            "/",
            "/dashboard",
            "/dashboard/legacy",
        ),
    )

    # ── token 解析（供 logout 等场景复用；鉴权主依赖已提炼至 jarvis/api/deps.py）──
    def _extract_token(request: Request) -> str:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[7:].strip()
        return request.query_params.get("token", "")

    # ── 能力服务实例（文件上传 / 联网搜索 / 图文识别）──
    upload_store = UploadStore()
    search_service = WebSearchService()
    vision_processor = build_vision_processor()
    app.extra["upload_store"] = upload_store
    app.extra["search_service"] = search_service
    app.extra["vision_processor"] = vision_processor

    if court is None:
        court = Court()

    if config is not None and config.genome_path:
        court._sm.genome_path = config.genome_path

    # Inject eval_runner / audit_logger / template_manager into app.extra for dashboard endpoints
    app.extra["eval_runner"] = eval_runner
    app.extra["audit_logger"] = audit_logger
    app.extra["template_manager"] = template_manager

    # Inject P0 governance modules
    app.extra["governance_agent"] = governance_agent
    app.extra["bounded_autonomy_engine"] = bounded_autonomy_engine
    app.extra["recovery_engine"] = recovery_engine

    # Inject P1 modules
    app.extra["tool_guard_middleware"] = ToolGuardMiddleware()
    app.extra["hallucination_detector"] = HallucinationDetector(governance_agent=governance_agent)
    app.extra["prompt_guard"] = PromptGuard(severity_threshold="warn")

    # Inject RBAC Engine (may be overridden by Emperor.serve())
    from jarvis.rbac import RBACEngine
    app.extra["rbac_engine"] = RBACEngine()

    # Inject Hierarchical Memory Engine
    if hierarchical_memory_engine is None:
        hierarchical_memory_engine = HierarchicalMemoryEngine()
    app.extra["hierarchical_memory_engine"] = hierarchical_memory_engine

    # ── Endpoints ──────────────────────────────────────────────────

    @app.get("/")
    def root():
        return {
            "service": "emperor-court",
            "status": "ok",
            "config_loaded": config is not None,
        }

    @app.get("/health")
    def health():
        """容器 / 云平台健康探针（liveness probe）。

        刻意做成"零依赖"实现：不触碰数据库、不加载大臣、不 import 任何
        重依赖，保证进程一起来就能返回 200。供 Dockerfile HEALTHCHECK、
        docker compose healthcheck、Render healthCheckPath、K8s 探针使用。
        需要深度健康信息（组件级）请用 ``GET /api/health``。
        """
        return {"status": "ok", "service": "emperor-core"}

    @app.get("/court/summary")
    def get_summary():
        return {"summary": court.summary()}

    @app.get("/court/snapshot")
    def get_snapshot():
        return court.inspect.snapshot()

    @app.get("/court/history")
    def get_history():
        records = [court.history[i] for i in range(len(court.history))]
        return {"cycles": len(records), "records": records}

    @app.get("/court/ministers")
    def list_ministers():
        snap = court.inspect.snapshot()
        active = [m.name for m in snap.ministers if m.status == "active"]
        return {"active": active, "total": snap.total_ministers}

    @app.get("/court/minister/{name}")
    def get_minister(name: str):
        detail = court.inspect.minister_detail(name)
        if detail is None:
            raise HTTPException(404, f"Minister '{name}' not found")
        return {"detail": detail}

    @app.post("/court/register")
    def register_minister(req: RegisterRequest):
        name = court.register(
            name=req.name, domain=req.domain,
            temperature=req.temperature,
            confidence_baseline=req.confidence_baseline,
        )
        return {"name": name}

    @app.post("/court/register/batch")
    def register_batch(req: BulkRegisterRequest):
        specs = [
            {"name": m.name, "domain": m.domain,
             "temperature": m.temperature,
             "confidence_baseline": m.confidence_baseline}
            for m in req.ministers
        ]
        names = court.register_many(specs)
        return {"names": names, "count": len(names)}

    @app.post("/court/evolve")
    def run_evolution(req: EvolveRequest):
        return court.evolve(req.cycles)

    @app.post("/court/dispatch")
    def record_dispatch(req: DispatchRequest):
        # ── RBAC permission check ──
        rbac_engine: Optional[RBACEngine] = app.extra.get("rbac_engine")
        if rbac_engine is not None:
            perm = intent_to_permission(req.intent)
            if perm is None:
                logger.warning(
                    "Unknown intent '%s' — no matching Permission, skipping RBAC",
                    req.intent,
                )
            elif not rbac_engine.check_permission(req.minister, perm):
                role = rbac_engine.get_role(req.minister)
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": (
                            f"Permission '{perm.name}' denied for minister "
                            f"'{req.minister}' (role: {role.name})"
                        ),
                    },
                )

        # P0 Prompt Injection guard: scan user input before dispatch
        prompt_guard: PromptGuard = app.extra.get("prompt_guard")
        if prompt_guard is not None:
            scan_result = prompt_guard.scan_input(req.intent)
            if scan_result.level == "dangerous":
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "Prompt injection detected",
                        "scan_result": scan_result.to_dict(),
                    },
                )
            elif scan_result.level == "suspicious":
                logger.warning(
                    "Suspicious input detected in dispatch (minister=%s): %s",
                    req.minister,
                    scan_result.reason,
                )

        court.record_dispatch(
            req.minister, req.edict_id, req.intent,
            req.success, req.confidence,
            execution_time_ms=req.execution_time_ms,
        )
        # Real-time event
        from jarvis.event_publisher import publish_dispatch
        publish_dispatch(req.minister, req.edict_id, req.intent,
                         req.success, req.confidence, req.execution_time_ms)
        return {
            "message": "Dispatch recorded",
            "security": scan_result.to_dict() if (prompt_guard is not None and scan_result.level != "harmless") else None,
        }

    @app.post("/court/feedback")
    def record_feedback(req: FeedbackRequest):
        ok = court.record_feedback(req.minister, req.edict_id, req.score)
        if not ok:
            raise HTTPException(404, "Dispatch not found")
        return {"message": "Feedback recorded"}

    @app.post("/court/genomes/save")
    def save_genomes():
        path = court.save_genomes()
        if path is None:
            raise HTTPException(400, "No genome_path configured")
        return {"path": path}

    @app.post("/court/genomes/load")
    def load_genomes(req: GenomeLoadRequest):
        genomes, meta = court.load_genomes(req.path)
        return {
            "loaded": len(genomes),
            "metadata": meta,
            "active": court.active_ministers,
        }

    @app.post("/court/config/load")
    def load_config(req: ConfigLoadRequest):
        try:
            cfg = SurvivalConfig.from_yaml(req.path)
        except FileNotFoundError:
            raise HTTPException(404, f"Config file not found: {req.path}")
        if cfg.genome_path:
            court._sm.genome_path = cfg.genome_path
        return {"message": "Config loaded",
                "fields": list(cfg.__dataclass_fields__)}

    @app.get("/court/config")
    def get_config():
        gp = getattr(court._sm, "genome_path", None)
        return {
            "configured": config is not None or bool(gp),
            "genome_path": gp,
        }

    # ── Dashboard ───────────────────────────────────────────────────

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard():
        """Serve the ChatGPT-style chat + evolution-insight dashboard."""
        from jarvis.chat_dashboard import generate_chat_html
        return generate_chat_html(
            api_base=f"http://{app.extra.get('host', '127.0.0.1')}:{app.extra.get('port', 9020)}"
        )

    @app.get("/dashboard/legacy", response_class=HTMLResponse)
    def dashboard_legacy():
        """原监控大盘（保留入口，便于访问大臣 CRUD / 自愈 / 调度配置等高级功能）。"""
        from jarvis.dashboard_html import generate_html
        return generate_html(api_base=f"http://{app.extra.get('host', '127.0.0.1')}:{app.extra.get('port', 9020)}")

    @app.get("/dashboard/status")
    def dashboard_status():
        """Aggregated status for the dashboard frontend."""
        snap = court.inspect.snapshot()
        ranking = court.merit_ranking if hasattr(court, 'merit_ranking') else []

        ministers = []
        for m in snap.ministers:
            ministers.append({
                "name": m.name,
                "domain": getattr(m, "domain", "general"),
                "merit": getattr(m, "merit", 0.0),
                "confidence": getattr(m, "confidence", 0.0),
                "tasks_completed": getattr(m, "tasks_completed", 0),
                "success_rate": getattr(m, "success_rate", 0.0),
                "status": m.status,
            })

        # Sort by merit descending
        ministers.sort(key=lambda x: x["merit"], reverse=True)

        result = {
            "court": {
                "active_ministers": snap.active_count,
                "total_ministers": snap.total_ministers,
                "cycle": getattr(court, "cycle", 0),
                "top_minister": str(ranking[0]) if ranking else "none",
            },
            "ministers": ministers,
            "tasks": {
                "total": getattr(court, "_total_tasks", 0),
                "completed": getattr(court, "_completed_tasks", 0),
                "failed": getattr(court, "_failed_tasks", 0),
                "success_rate": getattr(court, "success_rate", 0.0),
                "avg_merit": getattr(court, "avg_merit", 0.0),
            },
            "config": {
                "min_ministers": getattr(court, "min_ministers", 0),
                "max_ministers": getattr(court, "max_ministers", 0),
                "crossover_rate": getattr(court, "crossover_rate", 0.0),
                "api_port": app.extra.get("port", 9020),
            },
            "scheduler_running": app.extra.get("scheduler_running", False),
            "scheduler_jobs": app.extra.get("scheduler_jobs", 0),
            "scheduler_total_runs": app.extra.get("scheduler_total_runs", 0),
        }
        return result

    @app.get("/dashboard/alerts")
    def dashboard_alerts():
        """Alert history and active rules for the dashboard."""
        mgr = app.extra.get("alert_manager")
        if mgr is None:
            return {"history": [], "rules": []}

        return {
            "history": [
                {
                    "rule_name": a.rule_name,
                    "severity": a.severity,
                    "message": a.message,
                    "metric": a.metric,
                    "current_value": a.current_value,
                    "threshold": a.threshold,
                    "operator": a.operator,
                    "timestamp": a.timestamp,
                }
                for a in mgr.history(limit=50)
            ],
            "rules": [
                {
                    "name": r.name,
                    "metric": r.metric,
                    "threshold": r.threshold,
                    "operator": r.operator,
                    "severity": r.severity,
                    "message": r.message,
                    "enabled": r.enabled,
                    "tags": r.tags,
                }
                for r in mgr.list_rules()
            ],
        }

    @app.get("/dashboard/metrics")
    def dashboard_metrics():
        """Performance metrics for the dashboard timeseries."""
        mp = app.extra.get("metrics_plugin")
        if mp is None:
            return {"summary": {}, "tasks": [], "evolutions": []}

        sn = court.inspect.snapshot()
        s = mp.summary(active_ministers=sn.active_count)

        tasks = []
        for t in mp.task_history(limit=100):
            tasks.append({
                "task_id": t.task_id,
                "timestamp": t.timestamp,
                "success": t.success,
                "confidence": t.confidence,
                "execution_time_ms": t.execution_time_ms,
                "domain": t.domain,
                "error": t.error,
            })

        evos = []
        for e in mp.evolution_history(limit=50):
            evos.append({
                "timestamp": e.timestamp,
                "cycles": e.cycles,
                "active_ministers": e.active_ministers,
                "avg_merit": e.avg_merit,
            })

        return {
            "summary": {
                "total_tasks": s.total_tasks,
                "successful_tasks": s.successful_tasks,
                "failed_tasks": s.failed_tasks,
                "success_rate": s.success_rate,
                "avg_confidence": s.avg_confidence,
                "avg_execution_time_ms": s.avg_execution_time_ms,
                "total_evolutions": s.total_evolutions,
                "total_evolution_cycles": s.total_evolution_cycles,
                "active_ministers": s.active_ministers,
                "time_window_seconds": s.time_window_seconds,
                "samples_in_buffer": s.samples_in_buffer,
            },
            "tasks": tasks,
            "evolutions": evos,
        }

    # ── Dashboard summary (aggregated stats bar) ──────────────────

    @app.get("/api/dashboard/summary")
    def dashboard_summary():
        """聚合指标摘要：活跃 Minister / 成功率 / 活动告警 / 今日自愈 / 今日 Pipeline / 运行时长"""
        import time as _time
        from datetime import datetime as _datetime
        from jarvis.health import get_uptime_seconds

        now = _time.time()
        today = _datetime.now().date()

        # ─── active ministers ───
        snap = court.inspect.snapshot()
        active_ministers = snap.active_count

        # ─── success rate（全局累计，与学习曲线口径一致）───
        # 左栏主口径 = court.success_rate（merit_board 全量累计），与
        # /api/dashboard/evolution-learning-curve 的 success_rate 同源，避免
        # 「左栏最近成功率」与「曲线平均成功率」两套数字互相打架。
        success_rate = round(float(getattr(court, "success_rate", 0.0)) * 100, 1)

        # ─── success rate (last hour) — 副指标，保留供 hover 显示 ───
        db = app.extra.get("db")
        success_rate_1h = 0.0
        if db is not None:
            tasks = db.get_task_history(limit=10000)
            recent = [t for t in tasks if (now - t.get("timestamp", 0)) < 3600]
            total = len(recent)
            if total > 0:
                success_rate_1h = round(sum(1 for t in recent if t.get("success")) / total * 100, 1)

        # ─── active alerts (last hour) ───
        mgr = app.extra.get("alert_manager")
        active_alerts = 0
        if mgr is not None:
            active_alerts = sum(1 for a in mgr._fired_history if now - a.timestamp < 3600)

        # ─── healings today ───
        emperor = app.extra.get("emperor")
        healings_today = 0
        if emperor is not None:
            healer = emperor.healing
            records = healer.history(limit=500)
            healings_today = sum(
                1 for r in records
                if _datetime.fromtimestamp(r.timestamp).date() == today
            )

        # ─── pipelines today ───
        ps = app.extra.get("pipeline_store")
        pipelines_today = 0
        if ps is not None:
            all_p = ps.get_recent(limit=200)
            pipelines_today = sum(
                1 for p in all_p
                if _datetime.fromtimestamp(p["created_at"]).date() == today
            )

        return {
            "active_ministers": active_ministers,
            "success_rate": success_rate,
            "success_rate_1h": success_rate_1h,
            "active_alerts": active_alerts,
            "healings_today": healings_today,
            "pipelines_today": pipelines_today,
            "uptime_seconds": round(get_uptime_seconds(), 0),
        }

    # ── Dashboard control panel endpoints ──────────────────────────

    @app.post("/dashboard/evolve")
    def dashboard_evolve():
        """Manually trigger evolution cycles."""
        emperor = app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(503, "Emperor not available")
        try:
            result = emperor.evolve(cycles=1)
        except Exception as e:
            raise HTTPException(500, f"Evolution failed: {e}")
        return {
            "ok": True,
            "generation": result.get("generation", 0),
            "count": result.get("count", 0),
        }

    @app.post("/dashboard/execute")
    def dashboard_execute(req: DashboardExecuteRequest):
        """Manually execute a task."""
        emperor = app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(503, "Emperor not available")
        prompt = req.prompt
        domain = req.domain or "general"
        if not prompt:
            raise HTTPException(400, "prompt is required")
        try:
            result = emperor.execute_task(prompt, domain=domain)
        except Exception as e:
            raise HTTPException(500, f"Task execution failed: {e}")
        return {
            "ok": True,
            "task_id": result.get("task_id", ""),
            "minister": result.get("minister", ""),
            "confidence": result.get("confidence", 0.0),
        }

    @app.post("/api/manual_task")
    def manual_task(req: ManualTaskRequest):
        """Execute a manual task with inline form submission. Returns report + id."""
        prompt = req.prompt.strip()
        if not prompt:
            raise HTTPException(400, "任务描述不能为空")

        emperor = app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(503, "Emperor not available")

        try:
            result = emperor.execute_task(prompt, domain=req.domain)
        except Exception as e:
            raise HTTPException(500, f"Task execution failed: {e}")

        return {
            "report": result.get("response", ""),
            "id": result.get("task_id", ""),
        }

    # ── 学习曲线 / 多轮运行 / 对话 端点 ──────────────────────────

    @app.get("/api/evolution/learning-curve")
    def evolution_learning_curve():
        """返回自进化学习曲线的完整时间序列（跨重启持久化）。

        结构：
          { "rounds": int,
            "points": [ {round, ts, avg_merit, success_rate, active_ministers,
                         ministers: {name: {merit, success_rate, tasks, domain}}} ] }
        """
        from jarvis.learning_curve import get_learning_curve
        return get_learning_curve()

    def _run_evolution_worker(cycles: int):
        """后台线程：逐轮进化并在每轮后记录一个学习曲线点。

        之所以每轮单独 ``emperor.evolve(cycles=1)`` 而不是一次性
        ``evolve(cycles=N)``，是因为后者只在末尾记 1 个点，看不出收敛过程。
        逐轮调用才能画出有形状的学习曲线。
        """
        global _evo_job
        from jarvis.learning_curve import get_learning_curve
        emperor = app.extra.get("emperor")
        _evo_job["rounds_total"] = cycles
        _evo_job["rounds_done"] = 0
        _evo_job["last_error"] = None
        _evo_job["finished_at"] = None
        _evo_job["started_at"] = time.time()
        if emperor is None:
            _evo_job["last_error"] = "Emperor not available"
            _evo_job["running"] = False
            _evo_job["finished_at"] = time.time()
            return
        try:
            for i in range(cycles):
                emperor.evolve(cycles=1)
                _evo_job["rounds_done"] = i + 1
            _evo_job["last_recorded_round"] = get_learning_curve()["rounds"]
        except Exception as e:  # noqa: BLE001
            _evo_job["last_error"] = str(e)
        finally:
            _evo_job["running"] = False
            _evo_job["finished_at"] = time.time()

    @app.post("/api/evolution/run")
    def evolution_run(req: EvolutionRunRequest):
        """手动触发 N 轮进化（多轮运行），后台异步执行，立即返回。

        前端轮询 ``GET /api/evolution/status`` 获取进度。每轮结束会往学习曲线
        记一个点，因此跑 5 轮会画出 5 个点。
        """
        with _evo_lock:
            if _evo_job["running"]:
                return {
                    "ok": True,
                    "accepted": True,
                    "already_running": True,
                    "status": dict(_evo_job),
                }
            _evo_job["running"] = True
        t = threading.Thread(
            target=_run_evolution_worker, args=(req.cycles,), daemon=True
        )
        t.start()
        return {"ok": True, "accepted": True, "cycles": req.cycles}

    @app.get("/api/evolution/status")
    def evolution_status():
        """返回后台进化任务的实时进度（前端轮询用）。"""
        return dict(_evo_job)

    @app.get("/api/llm/status")
    def llm_status():
        """返回当前 LLM 后端状态（模型 / 是否 live / 熔断），供 UI 显示模型徽标。"""
        try:
            mgr = _get_llm_manager()
            stats = mgr.get_stats()
            live = [b for b in stats.get("backends", []) if b.get("live")]
            return {
                "mock_mode": mgr.mock_mode,
                "model": (live[0]["model"] if live else "mock"),
                "provider": (live[0]["provider"] if live else "mock"),
                "backends": stats.get("backends", []),
                "last_latency_ms": stats.get("last_latency_ms"),
            }
        except Exception as e:
            return {"mock_mode": True, "model": "unknown", "error": str(e)}

    # ══════════════════════════════════════════════════════════════════
    # 多用户：鉴权 / 会话 / token 用量
    # ══════════════════════════════════════════════════════════════════
    @app.post("/api/auth/register")
    def auth_register(req: AuthRequest):
        """开放注册（EMPEROR_OPEN_REGISTRATION 开关，默认 1）。注册成功即自动登录。"""
        open_flag = str(os.getenv("EMPEROR_OPEN_REGISTRATION", "1") or "1").strip().lower()
        if open_flag not in ("1", "true", "yes", "on"):
            raise HTTPException(403, "注册已关闭（管理员可在环境变量中开放）")
        username = (req.username or "").strip()
        if not username or not req.password:
            raise HTTPException(400, "用户名与密码均不能为空")
        if len(req.password) < 6:
            raise HTTPException(400, "密码至少 6 位")
        if auth_store.get_user_by_username(username) is not None:
            raise HTTPException(409, "用户名已存在")
        try:
            uid = auth_store.create_user(username, req.password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        token = auth_store.create_session(uid)
        user = auth_store.get_user(uid)
        return {"ok": True, "token": token, "user": user}

    @app.post("/api/auth/login")
    def auth_login(req: AuthRequest):
        user = auth_store.verify_user(req.username, req.password)
        if user is None:
            raise HTTPException(401, "用户名或密码错误")
        token = auth_store.create_session(user["id"])
        return {"ok": True, "token": token, "user": user}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request):
        token = _extract_token(request)
        if token:
            auth_store.delete_session(token)
        return {"ok": True}

    @app.get("/api/me")
    def api_me(user: dict = Depends(get_current_user)):
        usage = auth_store.get_user_usage(user["id"])
        return {"ok": True, "user": user, "usage": usage}

    # ══════════════════════════════════════════════════════════════════
    # 能力：文件上传 / 下载 / 联网搜索 / 图文识别 / 管理员后台
    # ══════════════════════════════════════════════════════════════════
    @app.post("/api/upload")
    async def api_upload(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
        """上传文件（白名单 + MIME/扩展双验 + 大小限制 + UUID 重命名）。"""
        try:
            content = await file.read()
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "读取上传文件失败")
        try:
            meta = upload_store.save(
                user["id"], file.filename or "upload", content, file.content_type or ""
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            auth_store.add_capability_usage(user["id"], "upload", meta["size"], "bytes", meta["name"])
        except Exception:  # noqa: BLE001 - 计量失败不影响上传
            pass
        return {"ok": True, "file": meta}

    @app.get("/api/files/{file_id}")
    def api_get_file(file_id: str, user: dict = Depends(get_current_user)):
        """下载已上传文件（属主校验，越权一律 404）。"""
        path = upload_store.resolve(file_id)
        meta = upload_store.get_meta(file_id)
        if path is None or meta is None or meta.get("user_id") != user["id"]:
            raise HTTPException(404, "文件不存在或无权访问")
        return FileResponse(
            path,
            media_type=meta.get("content_type") or "application/octet-stream",
            filename=meta.get("name"),
        )

    @app.post("/api/search")
    def api_search(req: SearchRequest, user: dict = Depends(get_current_user)):
        """真实联网搜索（DuckDuckGo 多 backend 容错），无网络/无库时结构化降级。"""
        results, degraded, reason = search_service.search(req.query, max_results=req.limit)
        if results:
            try:
                auth_store.add_capability_usage(
                    user["id"], "search", len(results), "calls", req.query[:200]
                )
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "results": results, "degraded": degraded, "reason": reason}

    @app.post("/api/vision")
    def api_vision(req: VisionRequest, user: dict = Depends(get_current_user)):
        """图文识别：图片 URL 或已上传图片文件 → 结构化文字描述。"""
        if vision_processor is None:
            return {
                "ok": True,
                "caption": "视觉识别不可用：未配置 vision 模型密钥（如 GROQ_API_KEY）",
                "raw": "",
                "usage": {},
                "degraded": True,
            }
        image_input: Optional[str] = None
        if req.file_id:
            path = upload_store.resolve(req.file_id)
            meta = upload_store.get_meta(req.file_id)
            if path is None or meta is None or meta.get("user_id") != user["id"]:
                raise HTTPException(404, "文件不存在或无权访问")
            image_input = str(path)
        elif req.image_url:
            image_input = req.image_url
        if not image_input:
            raise HTTPException(400, "请提供 image_url 或 file_id")
        try:
            result = vision_processor.process(image_input, prompt=req.prompt)
        except Exception as e:  # noqa: BLE001 - 视觉调用失败也降级，绝不 500
            logger.warning("视觉识别调用失败：%s", e, exc_info=True)
            return {
                "ok": True,
                "caption": f"视觉识别失败：{e}",
                "raw": "",
                "usage": {},
                "degraded": True,
            }
        backend = getattr(vision_processor, "_llm", None)
        usage = dict(getattr(backend, "last_usage", {}) or {})
        try:
            auth_store.add_capability_usage(user["id"], "vision", 1, "calls", req.prompt[:200])
            pt = int(usage.get("prompt_tokens", 0) or 0)
            ct = int(usage.get("completion_tokens", 0) or 0)
            if pt or ct:
                auth_store.add_token_usage(user["id"], pt, ct)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "caption": result.get("caption", ""), "raw": result.get("raw", ""), "usage": usage}

    @app.get("/api/admin/users")
    def admin_list_users(admin: dict = Depends(require_admin)):
        """管理员：用户列表。"""
        return {"ok": True, "users": auth_store.list_users()}

    @app.post("/api/admin/users/{user_id}/ban")
    def admin_ban(user_id: int, req: AdminSetBannedRequest, admin: dict = Depends(require_admin)):
        if not auth_store.set_user_banned(user_id, req.banned):
            raise HTTPException(404, "用户不存在")
        return {"ok": True}

    @app.post("/api/admin/users/{user_id}/unban")
    def admin_unban(user_id: int, admin: dict = Depends(require_admin)):
        if not auth_store.set_user_banned(user_id, False):
            raise HTTPException(404, "用户不存在")
        return {"ok": True}

    @app.post("/api/admin/users/{user_id}/password")
    def admin_reset_password(user_id: int, req: AdminResetPasswordRequest, admin: dict = Depends(require_admin)):
        if not auth_store.set_user_password(user_id, req.password):
            raise HTTPException(404, "用户不存在")
        return {"ok": True}

    @app.put("/api/admin/users/{user_id}/quota")
    def admin_set_quota(user_id: int, req: AdminSetQuotaRequest, admin: dict = Depends(require_admin)):
        if not auth_store.set_user_quota(user_id, req.quota):
            raise HTTPException(404, "用户不存在")
        return {"ok": True}

    @app.get("/api/conversations")
    def list_convs(user: dict = Depends(get_current_user)):
        return {"ok": True, "conversations": auth_store.list_conversations(user["id"])}

    @app.post("/api/conversations")
    def create_conv(req: ConversationCreate, user: dict = Depends(get_current_user)):
        cid = auth_store.create_conversation(user["id"], req.title)
        return {"ok": True, "id": cid}

    @app.get("/api/conversations/{conv_id}/messages")
    def get_conv_messages(conv_id: int, user: dict = Depends(get_current_user)):
        if auth_store.get_conversation(conv_id, user["id"]) is None:
            raise HTTPException(404, "会话不存在或无权访问")
        return {"ok": True, "messages": auth_store.list_messages(conv_id)}

    @app.put("/api/conversations/{conv_id}")
    def rename_conv(conv_id: int, req: ConversationRename, user: dict = Depends(get_current_user)):
        if not auth_store.rename_conversation(conv_id, user["id"], req.title):
            raise HTTPException(404, "会话不存在或无权访问")
        return {"ok": True}

    @app.delete("/api/conversations/{conv_id}")
    def delete_conv(conv_id: int, user: dict = Depends(get_current_user)):
        if not auth_store.delete_conversation(conv_id, user["id"]):
            raise HTTPException(404, "会话不存在或无权访问")
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
        """ChatGPT 风格流式对话端点（SSE）。

        走真实多后端 LLM（已接 NVIDIA NIM 时即为真模型推理）；以打字机方式
        把完整回复分块推送，获得类 ChatGPT 的流式体验（无需后端原生 streaming）。
        支持多会话持久化：提供 ``conversation_id`` 时从数据库加载历史、保存消息并计量 token。
        """
        import asyncio
        import json as _json

        mgr = _get_llm_manager()
        prompt = req.message.strip()
        if not prompt and not req.file_id and not req.image_url:
            raise HTTPException(400, "message is required")

        # 视觉 caption 抽取（图片 URL / 图片文件共用；失败降级为可读文案）
        def _vision_caption(image_input: str) -> str:
            if vision_processor is None:
                return "[视觉识别不可用：未配置 vision 模型密钥（如 GROQ_API_KEY）]"
            try:
                result = vision_processor.process(
                    image_input, prompt="请详细描述这张图片，并提取图中可见的文字。"
                )
                caption = result.get("caption", "")
                backend = getattr(vision_processor, "_llm", None)
                usage = dict(getattr(backend, "last_usage", {}) or {})
                try:
                    auth_store.add_capability_usage(user["id"], "vision", 1, "calls", "")
                    pt = int(usage.get("prompt_tokens", 0) or 0)
                    ct = int(usage.get("completion_tokens", 0) or 0)
                    if pt or ct:
                        auth_store.add_token_usage(user["id"], pt, ct)
                except Exception:  # noqa: BLE001
                    pass
                return caption
            except Exception as e:  # noqa: BLE001
                logger.warning("聊天视觉识别失败：%s", e)
                return f"[视觉识别失败：{e}]"

        # 历史来源：优先数据库持久化会话，否则用前端内存态 history（兼容）
        conv_id = req.conversation_id
        if conv_id is not None:
            conv = auth_store.get_conversation(conv_id, user["id"])
            if conv is None:
                raise HTTPException(404, "会话不存在或无权访问")
            history_ctx = auth_store.list_messages(conv_id, limit=20)
        else:
            history_ctx = req.history or []

        async def generate():
            # 用户消息开头即落库，避免「新建对话后看不到历史」
            if conv_id is not None and user.get("id"):
                try:
                    auth_store.add_message(conv_id, "user", prompt, 0, 0)
                except Exception:  # noqa: BLE001
                    pass

            final_prompt = prompt or "请分析我上传的文件/图片。"
            sources: list[dict] = []
            context_blocks: list[str] = []
            web_search_degraded = False
            web_search_reason = ""
            search_query_used = ""

            # ① 联网搜索：改写 query → 搜索 → 注入结果上下文 + 下发 sources/degraded 事件
            if req.web_search and prompt:
                # 1) 查询改写：长 prompt 用 LLM 改成干净搜索词，短 prompt 直接用
                #    用户口语化提问（如"我要求的AI领域"）会污染搜索引擎分词，
                #    导致搜出一堆无关结果 → LLM 机械复述 = "答非所问"。
                search_query = prompt.strip()
                if len(search_query) > 8:
                    try:
                        rewrite_resp = await mgr.complete(
                            "用户的口语化提问：\n"
                            f"{prompt}\n\n"
                            "请改写成一个简洁、适合搜索引擎的中文关键词查询（≤15字）。\n"
                            "要求：去掉口语化表达（'我'/'你'/'帮'/'吗'/'请'/'的'/'能'等），"
                            "聚焦核心信息需求，保留关键实体。\n"
                            "只输出改写后的查询词，不要任何标点符号、引号或解释。",
                            system="你是查询改写助手，只输出改写后的查询词本身。",
                        )
                        new_q = (rewrite_resp or "").strip().strip('"\'【】「」“”‘’')
                        if "\n" not in new_q and 2 <= len(new_q) <= 20 and not new_q.startswith(("用户", "请", "原", "提问")):
                            search_query = new_q
                    except Exception:
                        pass  # 改写失败 → 用原 prompt
                search_query_used = search_query

                # 2) 搜索
                results, degraded, reason = search_service.search(search_query, max_results=5)
                if results:
                    sources = [
                        {"title": r.get("title", ""), "url": r.get("url", "")}
                        for r in results
                        if r.get("url")
                    ]
                    ctx_lines = [
                        f"[{i + 1}] {r.get('title', '')}\n{r.get('url', '')}\n{r.get('snippet', '')}"
                        for i, r in enumerate(results)
                    ]
                    context_blocks.append(
                        "【联网搜索】\n"
                        f"用户原问题：{prompt}\n"
                        f"已用查询词「{search_query}」检索到 {len(results)} 条结果。\n"
                        "⚠️ 重要约束：\n"
                        "1. 先逐条判断每条结果是否真正与用户问题相关；"
                        "若不相关，直接忽略，不要为凑数而复述。\n"
                        "2. 若全部结果都不相关，请明确告知用户「未能从联网搜索中找到相关信息」，"
                        "并建议用户换更具体的关键词；不要勉强回答、不要编造。\n"
                        "3. 基于相关信息做有深度的综合分析和总结，不要直接复述原文。\n\n"
                        "结果列表：\n" + "\n\n".join(ctx_lines)
                    )
                    try:
                        auth_store.add_capability_usage(
                            user["id"], "search", len(results), "calls", req.message[:200]
                        )
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    # 搜索失败：给 LLM 明确硬约束 + 给前端 degraded 事件，
                    # 避免 LLM 在没有真实来源的情况下自由发挥（编论文/编链接）。
                    web_search_degraded = True
                    web_search_reason = reason or "搜索服务暂不可用"
                    context_blocks.append(
                        "【联网搜索不可用】\n"
                        f"原因：{web_search_reason}。\n"
                        "⚠️ 严格约束：本次回答不得编造任何 URL、论文标题、新闻出处、人名/公司名/年份等事实。"
                        "若仅依据已有知识无法给出准确答案，请明确告知用户「该信息无法通过联网核实」，"
                        "并建议用户提供更具体的关键词或换用其他信息源。"
                    )

            # ② 文件 / 图片：注入视觉 caption 或文件文本
            if req.file_id:
                path = upload_store.resolve(req.file_id)
                meta = upload_store.get_meta(req.file_id)
                if path is not None and meta is not None and meta.get("user_id") == user["id"]:
                    ext = meta.get("ext", "")
                    if ext in _IMAGE_EXTS:
                        context_blocks.append("【图片识别结果】\n" + _vision_caption(str(path)))
                    else:
                        context_blocks.append("【文件内容】\n" + _read_file_text(str(path), ext))
            elif req.image_url:
                context_blocks.append("【图片识别结果】\n" + _vision_caption(req.image_url))

            if context_blocks:
                final_prompt = final_prompt + "\n\n" + "\n\n".join(context_blocks)

            if sources:
                yield f"data: {_json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
            if web_search_degraded:
                yield (
                    "data: "
                    + _json.dumps(
                        {
                            "search_degraded": True,
                            "reason": web_search_reason,
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )

            try:
                answer = await mgr.complete(final_prompt, system=req.system, history=history_ctx)
                usage = dict(getattr(mgr, "last_usage", {}) or {})
            except Exception as e:  # noqa: BLE001
                answer = f"[模型调用失败] {e}"
                usage = {}
            if not answer:
                answer = "(空响应)"
            # 打字机式分块推送（buffer 累积，便于中断时回写）
            buf = []
            step = 3
            try:
                for i in range(0, len(answer), step):
                    chunk = answer[i:i + step]
                    buf.append(chunk)
                    yield f"data: {_json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.012)
            finally:
                # 无论流是否被客户端中断（切会话/关页面），都按已累积内容落库
                pt = int(usage.get("prompt_tokens", 0) or 0)
                ct = int(usage.get("completion_tokens", 0) or 0)
                if conv_id is not None and user.get("id"):
                    try:
                        auth_store.add_message(conv_id, "assistant", "".join(buf), pt, ct)
                        auth_store.add_token_usage(user["id"], pt, ct)
                    except Exception:  # noqa: BLE001 - 计量失败绝不影响对话
                        pass
            yield "data: " + _json.dumps(
                {"usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}},
                ensure_ascii=False,
            ) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/dashboard/heal")
    def dashboard_heal():
        """Manually trigger self-healing check on recent alerts."""
        emperor = app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(503, "Emperor not available")
        try:
            # Collect unique alert rule names from recent alert history
            alert_mgr = emperor.alerts
            rule_names = list({a.rule_name for a in alert_mgr.history(limit=50)})
            records = emperor.healing.handle_batch(rule_names)
        except Exception as e:
            raise HTTPException(500, f"Healing check failed: {e}")
        return {
            "ok": True,
            "actions": [
                {
                    "action_name": r.action_name,
                    "alert_rule": r.alert_rule,
                    "success": r.success,
                    "error": r.error,
                }
                for r in records
            ],
        }

    @app.get("/dashboard/task-history")
    def dashboard_task_history(
        minister: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        """Return task history with optional filtering (newest first)."""
        db = app.extra.get("db")
        if db is None:
            return {"history": [], "note": "Database not initialized"}
        try:
            rows = db.get_task_history(
                limit=limit, minister=minister,
                status=status, search=search, offset=offset,
            )
            return {"history": rows, "count": len(rows)}
        except Exception as e:
            raise HTTPException(500, f"Failed to read task history: {e}")

    @app.get("/dashboard/evolution-history")
    def dashboard_evolution_history():
        """Return recent evolution history from the database (newest first)."""
        db = app.extra.get("db")
        if db is None:
            return {"history": [], "note": "Database not initialized"}
        try:
            rows = db.get_evolution_history(limit=100)
            return {"history": rows, "count": len(rows)}
        except Exception as e:
            raise HTTPException(500, f"Failed to read evolution history: {e}")

    @app.get("/dashboard/alert-history")
    def dashboard_alert_history(
        level: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        """Return alert history with optional filtering (newest first)."""
        db = app.extra.get("db")
        if db is None:
            return {"history": [], "note": "Database not initialized"}
        try:
            rows = db.get_alert_history(
                limit=limit, level=level, search=search, offset=offset,
            )
            return {"history": rows, "count": len(rows)}
        except Exception as e:
            raise HTTPException(500, f"Failed to read alert history: {e}")

    @app.get("/dashboard/export")
    def dashboard_export(
        format: str = "json",
        what: str = "all",
    ):
        """Export dashboard data in JSON or CSV format."""
        db = app.extra.get("db")
        if db is None:
            raise HTTPException(503, "Database not initialized")
        try:
            data = db.export_all()
        except Exception as e:
            raise HTTPException(500, f"Failed to export data: {e}")

        # Filter by what
        if what == "tasks":
            data = {"tasks": data["tasks"]}
        elif what == "alerts":
            data = {"alerts": data["alerts"]}
        elif what == "evolutions":
            data = {"evolutions": data["evolutions"]}

        if format == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)

            tables = [
                ("TASKS", data.get("tasks", [])),
                ("ALERTS", data.get("alerts", [])),
                ("EVOLUTIONS", data.get("evolutions", [])),
            ]

            first_section = True
            for section_name, rows in tables:
                if not rows:
                    continue
                if not first_section:
                    output.write("---\n")
                first_section = False

                # Header
                writer.writerow(rows[0].keys())
                for row in rows:
                    writer.writerow(row.values())

            from fastapi.responses import Response
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=emperor_export.csv"},
            )

        # JSON format
        return data

    # ── Minister management API ─────────────────────────────────────

    VALID_DOMAINS = ["general", "math", "data", "code", "legal", "science", "creative"]

    @app.get("/api/ministers")
    def api_list_ministers():
        """List all ministers with name, domain, merit, stability."""
        snap = court.inspect.snapshot()
        ministers = []
        for m in snap.ministers:
            genome = court._sm._genomes.get(m.name)
            merit = m.merit
            if genome is not None and hasattr(genome, "_merit_override"):
                merit = genome._merit_override

            # Extract task-feedback fields from genome (default 0 for legacy)
            success_streak = getattr(genome, "success_streak", 0)
            failure_streak = getattr(genome, "failure_streak", 0)
            total_tasks = getattr(genome, "total_tasks", 0)
            capability_hits = getattr(genome, "capability_hits", 0)

            ministers.append({
                "name": m.name,
                "domain": m.domain,
                "merit": round(merit, 1),
                "stability": round(getattr(m, "confidence_baseline", 0.75), 2),
                "success_streak": success_streak,
                "failure_streak": failure_streak,
                "total_tasks": total_tasks,
                "capability_hits": capability_hits,
            })
        return {"ministers": ministers}

    @app.post("/api/ministers")
    def api_create_minister(req: MinisterCreateRequest):
        """Create a new minister."""
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "名称不能为空")

        if name in court._sm._genomes:
            raise HTTPException(400, "大臣已存在")

        if req.domain not in VALID_DOMAINS:
            raise HTTPException(400, f"无效领域: {req.domain}")

        court.register(name=name, domain=req.domain)
        return {
            "minister": {
                "name": name,
                "domain": req.domain,
                "merit": 0.0,
                "stability": 0.75,
                "success_streak": 0,
                "failure_streak": 0,
                "total_tasks": 0,
                "capability_hits": 0,
            },
            "message": f"大臣 {name} 已创建",
        }

    @app.put("/api/ministers/{name}")
    def api_update_minister(name: str, req: MinisterUpdateRequest):
        """Update a minister's domain, merit, or stability."""
        genome = court._sm._genomes.get(name)
        if genome is None:
            raise HTTPException(404, f"大臣 {name} 不存在")

        updated = False

        if req.domain is not None:
            if req.domain not in VALID_DOMAINS:
                raise HTTPException(400, f"无效领域: {req.domain}")
            genome.domain = req.domain
            updated = True

        if req.merit is not None:
            if req.merit < 0:
                raise HTTPException(400, "功绩不能为负数")
            genome._merit_override = float(req.merit)
            updated = True

        if req.stability is not None:
            if req.stability < 0 or req.stability > 1:
                raise HTTPException(400, "稳定度必须在 0-1 之间")
            genome.confidence_baseline = float(req.stability)
            updated = True

        if not updated:
            raise HTTPException(400, "至少需要提供一个更新字段")

        # Recompute merit considering override
        merit = 0.0
        if hasattr(genome, "_merit_override"):
            merit = genome._merit_override
        elif court._sm._merit_board is not None:
            merit = court._sm._merit_board.compute_merit(name)

        return {
            "minister": {
                "name": name,
                "domain": genome.domain,
                "merit": round(merit, 1),
                "stability": round(genome.confidence_baseline, 2),
            },
        }

    @app.delete("/api/ministers/{name}")
    def api_delete_minister(name: str):
        """Delete a minister permanently."""
        if name not in court._sm._genomes:
            raise HTTPException(404, f"大臣 {name} 不存在")

        del court._sm._genomes[name]
        if name in court._sm._statuses:
            del court._sm._statuses[name]

        return {"message": f"大臣 {name} 已删除"}

    @app.get("/api/scheduler/config")
    def api_get_scheduler_config():
        """Return current scheduler configuration."""
        return {
            "evolve_interval_minutes": _scheduler_config["evolve_interval_minutes"],
            "task_interval_minutes": _scheduler_config["task_interval_minutes"],
            "auto_schedule": _scheduler_config["auto_schedule"],
        }

    @app.put("/api/scheduler/config")
    def api_put_scheduler_config(req: SchedulerConfigRequest):
        """Update scheduler configuration in real-time."""
        updated_fields: list[str] = []

        # Validate and update evolve_interval
        if req.evolve_interval_minutes is not None:
            val = float(req.evolve_interval_minutes)
            if val != int(val):
                raise HTTPException(400, "进化间隔必须为整数分钟")
            _scheduler_config["evolve_interval_minutes"] = val
            updated_fields.append("evolve_interval_minutes")

        # Validate and update task_interval
        if req.task_interval_minutes is not None:
            val = float(req.task_interval_minutes)
            if val != int(val):
                raise HTTPException(400, "任务间隔必须为整数分钟")
            _scheduler_config["task_interval_minutes"] = val
            updated_fields.append("task_interval_minutes")

        # Handle auto_schedule toggle
        if req.auto_schedule is not None:
            prev = _scheduler_config["auto_schedule"]
            _scheduler_config["auto_schedule"] = req.auto_schedule
            updated_fields.append("auto_schedule")

            # Apply to live scheduler if available
            sched = getattr(court, "scheduler", None)
            if sched is not None:
                if req.auto_schedule and not prev:
                    sched.resume()
                elif not req.auto_schedule and prev:
                    sched.pause()

        # Apply interval updates to live scheduler
        if ("evolve_interval_minutes" in updated_fields or
                "task_interval_minutes" in updated_fields):
            sched = getattr(court, "scheduler", None)
            if sched is not None:
                sched.update_config(
                    task_interval_seconds=(
                        _scheduler_config["task_interval_minutes"] * 60
                    ),
                    evolve_interval_seconds=(
                        _scheduler_config["evolve_interval_minutes"] * 60
                    ),
                )

        return {
            "config": dict(_scheduler_config),
            "updated": updated_fields,
        }

    # ── Dashboard config endpoint ─────────────────────────────────

    @app.get("/api/config")
    def api_get_config():
        """Return dashboard-visible configuration."""
        # Prefer _emperor_config (injected by Emperor.serve), fallback to app.extra
        app_cfg = _emperor_config
        if app_cfg is None:
            emperor = getattr(app, "extra", {}).get("emperor")
            app_cfg = getattr(emperor, "app_config", None) if emperor else None

        theme = "dark"
        refresh = 15
        if app_cfg is not None:
            theme = getattr(app_cfg.dashboard, "theme", "dark")
            refresh = getattr(app_cfg.dashboard, "refresh_interval_seconds", 15)

        return {
            "theme": theme,
            "refresh_interval_seconds": refresh,
        }

    @app.post("/api/theme")
    def api_set_theme(req: ThemeRequest):
        """Set dashboard theme and persist to jarvis.yaml."""
        import json as _json
        import os as _os

        theme = req.theme

        if theme not in ("dark", "light", "auto"):
            raise HTTPException(400, "Invalid theme. Use dark, light, or auto")

        global _emperor_config

        if _emperor_config is not None:
            _emperor_config.dashboard.theme = theme

        # Persist to jarvis.yaml
        config_path = "jarvis.yaml"
        if _os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                raw = _json.load(f)
            raw.setdefault("dashboard", {})["theme"] = theme
            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(raw, f, indent=2, ensure_ascii=False)

        return {"theme": theme, "status": "ok"}

    # ── Health monitoring endpoint ──────────────────────────────

    @app.get("/api/health")
    def health_check():
        """系统健康检查端点（CPU/内存/磁盘/运行时长）"""
        from jarvis.health import get_system_health

        return get_system_health()

    # ── Dashboard live data endpoint ────────────────────────────

    @app.get("/api/dashboard/live")
    def dashboard_live():
        """聚合天气和新闻实时数据"""
        from jarvis.capability import _weather_handler, _news_handler

        weather_city = "北京"
        if _emperor_config is not None:
            weather_city = getattr(_emperor_config.dashboard, "weather_city", "北京")

        weather_result = _weather_handler(weather_city + "天气")
        news_result = _news_handler("科技新闻")

        return {
            "weather": weather_result.get("data", {}),
            "weather_text": weather_result.get("result", "天气获取失败"),
            "news": news_result.get("data", {}),
            "news_text": news_result.get("result", "新闻获取失败"),
        }

    # ── Dashboard capability stats endpoint ──────────────────────

    @app.get("/api/dashboard/capability-stats")
    def capability_stats():
        """能力命中统计（饼图数据）"""
        db = app.extra.get("db")
        if db is None:
            return {"labels": [], "values": [], "total": 0}

        tasks = db.get_task_history(limit=10000)
        stats: dict[str, int] = {}
        for t in tasks:
            cap = t.get("capability", "") or "general"
            stats[cap] = stats.get(cap, 0) + 1

        sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)

        return {
            "labels": [s[0] for s in sorted_stats],
            "values": [s[1] for s in sorted_stats],
            "total": sum(s[1] for s in sorted_stats),
        }

    # ── Dashboard Evals endpoints ─────────────────────────────────

    @app.get("/api/dashboard/evals/report")
    def evals_report():
        """返回最近一次 eval 聚合报告。"""
        runner = app.extra.get("eval_runner")
        if runner is None:
            return {
                "total_suites": 0,
                "total_cases": 0,
                "passed": 0,
                "failed": 0,
                "errored": 0,
                "pass_rate": 0,
                "suites": [],
            }
        return runner.report()

    @app.post("/api/dashboard/evals/run")
    def evals_run():
        """运行所有内置评测套件，返回报告。"""
        runner = app.extra.get("eval_runner")
        if runner is None:
            raise HTTPException(status_code=503, detail="EvalRunner not available")

        try:
            from jarvis.eval import create_builtin_suites

            suites = create_builtin_suites()
            import time as _t2
            t0 = _t2.time()
            runner.run_all(suites)
            elapsed = (_t2.time() - t0) * 1000
            report = runner.report()

            # Real-time event
            from jarvis.event_publisher import publish_eval
            publish_eval("all", report["passed_count"], report["failed_count"], elapsed)

            return report
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── LLM Judge API endpoints ────────────────────────────────

    @app.post("/api/evals/judge")
    async def judge_evaluate(request: Request):
        """单条 LLM-as-Judge 评估。

        评估 Agent 输出文本的质量，返回 per-dimension 分数。
        """
        from jarvis.llm_judge import LLMJudge, JudgingCriteria

        body = await request.json()
        req = JudgeEvaluateRequest(**body)

        # Map string criteria to enum
        criteria_map = {c.value: c for c in JudgingCriteria}
        criteria = []
        for c_name in req.criteria:
            c_enum = criteria_map.get(c_name)
            if c_enum:
                criteria.append(c_enum)

        if not criteria:
            raise HTTPException(status_code=400, detail="No valid criteria specified")

        judge = LLMJudge()
        result = judge.evaluate(
            output=req.output,
            expected=req.expected,
            criteria=criteria,
        )
        return result.to_dict()

    @app.post("/api/evals/judge/compare")
    async def judge_compare(request: Request):
        """LLM-as-Judge 对比评估。

        对比两个候选输出，返回 winner 和各维度分数。
        """
        from jarvis.llm_judge import LLMJudge, JudgingCriteria

        body = await request.json()
        req = JudgeCompareRequest(**body)

        criteria_map = {c.value: c for c in JudgingCriteria}
        criteria = []
        for c_name in req.criteria:
            c_enum = criteria_map.get(c_name)
            if c_enum:
                criteria.append(c_enum)

        if not criteria:
            raise HTTPException(status_code=400, detail="No valid criteria specified")

        judge = LLMJudge()
        result = judge.compare(
            output_a=req.output_a,
            output_b=req.output_b,
            expected=req.expected,
            criteria=criteria,
        )
        return result.to_dict()

    # ── Dashboard Audit endpoints ─────────────────────────────────

    @app.get("/api/dashboard/model-costs")
    def model_costs():
        """Return model router cost statistics."""
        router = app.extra.get("model_router")
        if router is None:
            return {
                "total_requests": 0,
                "requests_by_tier": {"cheap": 0, "standard": 0, "premium": 0},
                "estimated_cost_saved": 0.0,
                "savings_percent": 0.0,
                "tier_distribution": {"cheap": 0, "standard": 0, "premium": 0},
                "router_enabled": False,
            }
        report = router.report()
        report["router_enabled"] = True
        return report

    def _serialize_audit_entry(entry: Any) -> dict:
        """Convert AuditEntry dataclass → JSON-safe dict."""
        return {
            "id": getattr(entry, "id", 0),
            "trace_id": entry.trace_id,
            "step": entry.step,
            "phase": entry.phase,
            "action": entry.action,
            "actor": entry.actor,
            "input_summary": entry.input_summary,
            "output_summary": entry.output_summary,
            "success": entry.success,
            "error_msg": entry.error_msg,
            "duration_ms": getattr(entry, "duration_ms", 0),
            "created_at": entry.created_at,
        }

    @app.get("/api/dashboard/audit/recent")
    def audit_recent(limit: int = 50):
        """返回最近 N 条审计记录。"""
        logger = app.extra.get("audit_logger")
        if logger is None:
            return {"entries": [], "total": 0}

        try:
            entries = logger.reader().query_recent(min(limit, 200))
            return {
                "entries": [_serialize_audit_entry(e) for e in entries],
                "total": len(entries),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/dashboard/audit/stats")
    def audit_stats():
        """返回审计统计摘要。"""
        logger = app.extra.get("audit_logger")
        if logger is None:
            return {
                "total_entries": 0,
                "successes": 0,
                "failures": 0,
                "success_rate": 0,
                "db_size_bytes": 0,
                "top_actions": [],
            }

        try:
            return logger.reader().get_stats()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/dashboard/audit/failures")
    def audit_failures(limit: int = 50):
        """返回最近失败记录列表。"""
        logger = app.extra.get("audit_logger")
        if logger is None:
            return {"entries": [], "total": 0}

        try:
            entries = logger.reader().query_failures(min(limit, 200))
            return {
                "entries": [_serialize_audit_entry(e) for e in entries],
                "total": len(entries),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Prompt Template Dashboard endpoints ────────────────────────

    def _get_template_manager():
        """Resolve template_manager: prefer app.extra, fallback to module-level."""
        mgr = app.extra.get("template_manager")
        if mgr is None:
            from jarvis.capability import get_template_manager
            mgr = get_template_manager()
        if mgr is None:
            raise HTTPException(status_code=503, detail="PromptTemplateManager not available")
        return mgr

    @app.get("/api/dashboard/templates")
    def list_templates():
        """返回所有 capability 模板及其版本和评分。"""
        mgr = _get_template_manager()
        return mgr.list_templates()

    @app.post("/api/dashboard/templates/optimize")
    def optimize_template(body: TemplateOptimizeRequest):
        """对指定 capability 执行自动优化。"""
        mgr = _get_template_manager()
        try:
            result = mgr.auto_optimize(body.capability)
            return {
                "capability": body.capability,
                "version": result.get("version"),
                "performance_score": result.get("performance_score"),
                "system_prompt": result.get("system_prompt"),
                "frozen": result.get("frozen", False),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/dashboard/templates/feedback")
    def record_feedback(body: TemplateFeedbackRequest):
        """记录用户反馈分数并更新 performance_score。"""
        mgr = _get_template_manager()
        try:
            result = mgr.record_feedback(body.capability, body.score)
            return {
                "capability": body.capability,
                "performance_score": result.get("performance_score"),
                "version": result.get("version"),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/dashboard/templates/rollback")
    def rollback_template(body: TemplateRollbackRequest):
        """回滚模板到指定历史版本。"""
        mgr = _get_template_manager()
        try:
            result = mgr.rollback(body.capability, body.version)
            return {
                "capability": body.capability,
                "version": result.get("version"),
                "performance_score": result.get("performance_score"),
                "system_prompt": result.get("system_prompt"),
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── SSE streaming endpoint ────────────────────────────────────

    @app.get("/api/events")
    def sse_events():
        """Server-Sent Events stream for real-time dashboard updates."""
        import json as _json  # local alias to avoid shadowing module-level

        from jarvis.event_bus import event_bus

        q, sub_id = event_bus.subscribe()

        def generate():
            try:
                # Initial connection event
                yield f"data: {_json.dumps({'type': 'connected', 'data': {}})}\n\n"

                while True:
                    try:
                        data = q.get(timeout=30)
                        yield f"data: {data}\n\n"
                    except Exception:
                        # timeout → send heartbeat to keep alive
                        yield f"data: {_json.dumps({'type': 'heartbeat', 'data': {}})}\n\n"
            except GeneratorExit:
                pass
            finally:
                event_bus.unsubscribe(sub_id)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Pipeline endpoints ─────────────────────────────────────

    class PipelineExecuteRequest(BaseModel):
        template: str = "daily_brief"
        context: dict = Field(default_factory=dict)

    @app.post("/api/pipelines/execute")
    def execute_pipeline(body: PipelineExecuteRequest):
        """执行服务流水线"""
        try:
            from jarvis.pipeline import pipeline_registry, PipelineStatus

            template = body.template
            context = body.context

            if template == "search_analyze":
                query = context.get("query", "")
                result = pipeline_registry.execute_template(template, context, query=query)
            else:
                result = pipeline_registry.execute_template(template, context)

            # Real-time event
            from jarvis.event_publisher import publish_pipeline
            publish_pipeline(template, result.pipeline_id,
                             result.status.value,
                             steps=len(result.stages),
                             elapsed_ms=round(result.finished_at - result.started_at, 2) * 1000)

            return {
                "status": result.status.value,
                "pipeline_name": result.pipeline_name,
                "pipeline_id": result.pipeline_id,
                "stages": [
                    {"name": s.stage_name, "status": s.status.value}
                    for s in result.stages
                ],
                "duration": round(result.finished_at - result.started_at, 2),
                "final_output": (
                    result.final_output
                    if result.status == PipelineStatus.COMPLETED else {}
                ),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/pipelines/history")
    def pipeline_history(limit: int = 20):
        """流水线执行历史"""
        from jarvis.pipeline import pipeline_registry

        return pipeline_registry.get_history(limit)

    @app.get("/api/pipelines/templates")
    def pipeline_templates():
        """可用的流水线模板列表"""
        from jarvis.pipeline import pipeline_registry

        return {"templates": list(pipeline_registry._templates.keys())}

    # ── Pipeline scheduler endpoints ─────────────────────────────

    @app.post("/api/pipelines/schedule")
    def add_pipeline_schedule():
        """添加流水线定时调度"""
        data = request.get_json() or {}
        template = data.get("template", "daily_brief")
        interval_minutes = data.get("interval_minutes", 1440)  # 默认每天
        context = data.get("context", {})
        cron_expr = data.get("cron_expr")  # 可选

        import uuid

        job_id = f"job_{uuid.uuid4().hex[:8]}"

        try:
            from jarvis.pipeline import pipeline_scheduler

            pipeline_scheduler.add_schedule(
                job_id=job_id,
                template_name=template,
                interval_minutes=interval_minutes,
                context=context,
                cron_expr=cron_expr,
            )
            return jsonify({"job_id": job_id, "status": "scheduled"})
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/pipelines/schedule/<job_id>")
    def remove_pipeline_schedule(job_id):
        """删除定时调度"""
        from jarvis.pipeline import pipeline_scheduler

        success = pipeline_scheduler.remove_schedule(job_id)
        return jsonify({"job_id": job_id, "removed": success})

    @app.post("/api/pipelines/schedule/<job_id>/toggle")
    def toggle_pipeline_schedule(job_id):
        """启用/禁用定时调度"""
        data = request.get_json() or {}
        enabled = data.get("enabled", True)

        from jarvis.pipeline import pipeline_scheduler

        if enabled:
            success = pipeline_scheduler.enable_job(job_id)
        else:
            success = pipeline_scheduler.disable_job(job_id)

        return jsonify({"job_id": job_id, "enabled": enabled, "success": success})

    @app.get("/api/pipelines/schedule")
    def list_pipeline_schedules():
        """列出所有定时调度"""
        from jarvis.pipeline import pipeline_scheduler

        return jsonify(pipeline_scheduler.get_jobs())

    # ── Pipeline Monitor API ──────────────────────────────────────

    @app.get("/api/pipelines/monitor/summary")
    def pipeline_monitor_summary():
        """流水线监控摘要：总览、活跃、成功率、时间线"""
        from jarvis.pipeline_monitor import pipeline_monitor
        return pipeline_monitor.get_summary()

    @app.get("/api/pipelines/monitor/dag/<pipeline_id>")
    def pipeline_monitor_dag(pipeline_id: str):
        """单条流水线的 DAG 详情（节点 + 边）"""
        from jarvis.pipeline_monitor import pipeline_monitor
        dag = pipeline_monitor.get_dag(pipeline_id)
        if dag is None:
            raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_id}' not found")
        return dag

    @app.get("/api/pipelines/monitor/timeline/<pipeline_id>")
    def pipeline_monitor_timeline(pipeline_id: str):
        """单条流水线的执行时间线"""
        from jarvis.pipeline_monitor import pipeline_monitor
        timeline = pipeline_monitor.get_timeline(pipeline_id)
        if timeline is None:
            raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_id}' not found")
        return {"pipeline_id": pipeline_id, "timeline": timeline}

    @app.get("/api/pipelines/monitor/live")
    def pipeline_monitor_live():
        """实时流水线状态（轻量轮询）"""
        from jarvis.pipeline_monitor import pipeline_monitor
        return pipeline_monitor.get_live()

    # ── Pipeline store query endpoints (Dashboard Pipeline panel) ──

    @app.get("/api/pipelines")
    def list_pipelines(limit: int = 10, status: str | None = None):
        """返回最近 N 条 pipeline 执行记录（最新在前）。

        Query params:
            limit: 最大返回条数，默认 10，上限 100
            status: 可选过滤（running / completed / failed）
        """
        from jarvis.pipeline_store import pipeline_store

        records = pipeline_store.get_recent(limit=limit, status=status)
        return {
            "total": pipeline_store.count,
            "limit": limit,
            "records": [
                {
                    "template": r["template"],
                    "pipeline_id": r["pipeline_id"],
                    "status": r["status"],
                    "steps": r["steps"],
                    "total_steps": r["total_steps"],
                    "elapsed_ms": r["elapsed_ms"],
                    "created_at": r["created_at"],
                }
                for r in records
            ],
        }

    @app.get("/api/pipelines/{pipeline_id}")
    def get_pipeline(pipeline_id: str):
        """返回单条 pipeline 详情（含步骤列表）。"""
        from jarvis.pipeline_store import pipeline_store

        record = pipeline_store.get_by_id(pipeline_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_id}' not found")
        return {
            "template": record["template"],
            "pipeline_id": record["pipeline_id"],
            "status": record["status"],
            "steps": record["steps"],
            "total_steps": record["total_steps"],
            "elapsed_ms": record["elapsed_ms"],
            "created_at": record["created_at"],
            "step_details": record.get("step_details", []),
        }

    # ── Background heartbeat thread ───────────────────────────────

    import threading
    import time as _time

    def _start_heartbeat():
        from jarvis.event_bus import event_bus

        def _beat():
            while True:
                _time.sleep(15)
                try:
                    event_bus.publish_heartbeat()
                except Exception:
                    pass

        t = threading.Thread(target=_beat, daemon=True)
        t.start()

    _start_heartbeat()

    # ══════════════════════════════════════════════════════════════
    # Plugin Marketplace API
    # ══════════════════════════════════════════════════════════════

    class PluginInstallRequest(BaseModel):
        plugin_id: str

    class PluginToggleRequest(BaseModel):
        plugin_id: str
        enabled: bool

    class PluginConfigRequest(BaseModel):
        plugin_id: str
        config: dict = Field(default_factory=dict)

    @app.get("/api/dashboard/plugins")
    def get_plugins(request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        return mp.report()

    @app.post("/api/dashboard/plugins/install")
    def install_plugin(payload: PluginInstallRequest, request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        try:
            return mp.install(payload.plugin_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/dashboard/plugins/uninstall")
    def uninstall_plugin(payload: PluginInstallRequest, request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        try:
            return mp.uninstall(payload.plugin_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/dashboard/plugins/toggle")
    def toggle_plugin(payload: PluginToggleRequest, request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        try:
            if payload.enabled:
                return mp.enable(payload.plugin_id)
            else:
                return mp.disable(payload.plugin_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/dashboard/plugins/config")
    def set_plugin_config(payload: PluginConfigRequest, request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        try:
            return mp.set_config(payload.plugin_id, payload.config)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    # ══════════════════════════════════════════════════════════════
    # Plugin System API (hot-load third-party plugins)
    # ══════════════════════════════════════════════════════════════

    class PluginLoadRequest(BaseModel):
        path: str = Field(..., description="Absolute path to the plugin .py file")

    @app.get("/api/plugins")
    def plugin_system_list(request: Request):
        """List all loaded third-party plugins."""
        mgr = request.app.extra.get("plugin_system")
        if mgr is None:
            raise HTTPException(status_code=503, detail="Plugin system not available")
        return {"plugins": mgr.list_plugins(), "count": mgr.plugin_count}

    @app.post("/api/plugins/load")
    def plugin_system_load(payload: PluginLoadRequest, request: Request):
        """Load a third-party plugin from a .py file path."""
        mgr = request.app.extra.get("plugin_system")
        if mgr is None:
            raise HTTPException(status_code=503, detail="Plugin system not available")
        try:
            instance = mgr.load_plugin(payload.path)
            manifest = instance.get_manifest()
            return {
                "ok": True,
                "name": manifest.name,
                "version": manifest.version,
                "author": manifest.author,
                "hooks": manifest.hooks,
            }
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/plugins/{name}")
    def plugin_system_unload(name: str, request: Request):
        """Unload a previously loaded third-party plugin by name."""
        mgr = request.app.extra.get("plugin_system")
        if mgr is None:
            raise HTTPException(status_code=503, detail="Plugin system not available")
        removed = mgr.unload(name)
        if removed is None:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        return {"ok": True, "name": name}

    # ══════════════════════════════════════════════════════════════

    class VersionSnapshotRequest(BaseModel):
        description: str = ""

    class VersionRollbackRequest(BaseModel):
        snapshot_id: str
        components: list[str] = Field(default_factory=list)

    @app.get("/api/dashboard/versions")
    def list_versions(request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        try:
            snapshots = v.list_snapshots(limit=30)
            return [
                {
                    "id": s.id,
                    "timestamp": s.timestamp,
                    "description": s.description,
                    "components": list(s.components.keys()),
                    "component_count": len(s.components),
                }
                for s in snapshots
            ]
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/dashboard/versions/snapshot")
    def create_snapshot(payload: VersionSnapshotRequest, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        try:
            snap = v.snapshot(description=payload.description or "Dashboard manual snapshot")
            return {
                "id": snap.id,
                "timestamp": snap.timestamp,
                "description": snap.description,
                "component_count": len(snap.components),
                "components": list(snap.components.keys()),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/dashboard/versions/<snapshot_id>")
    def get_version(snapshot_id: str, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        snap = v.get_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {snapshot_id}")
        return {
            "id": snap.id,
            "timestamp": snap.timestamp,
            "description": snap.description,
            "metadata": snap.metadata,
            "component_count": len(snap.components),
            "components": {c: {"name": s.name, "checksum": s.checksum} for c, s in snap.components.items()},
        }

    @app.get("/api/dashboard/versions/<snapshot_id>/diff")
    def diff_versions(snapshot_id: str, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        try:
            preview = v.preview_rollback(snapshot_id)
            return {
                "snapshot_id": preview.snapshot_id,
                "summary": preview.summary,
                "components": {
                    comp: {
                        "added_keys": d.added_keys,
                        "removed_keys": d.removed_keys,
                        "changed_keys": d.changed_keys,
                        "changes": d.changes,
                    }
                    for comp, d in preview.per_component.items()
                },
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/dashboard/versions/rollback")
    def rollback_version(payload: VersionRollbackRequest, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        try:
            components = payload.components if payload.components else None
            results = v.rollback(payload.snapshot_id, components=components)
            return {
                "snapshot_id": payload.snapshot_id,
                "results": results,
                "all_succeeded": all(results.values()) if results else False,
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.delete("/api/dashboard/versions/<snapshot_id>")
    def delete_version(snapshot_id: str, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        ok = v.delete_snapshot(snapshot_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {snapshot_id}")
        return {"deleted": True, "snapshot_id": snapshot_id}

    # ── HITL Approval endpoints ──

    @app.get("/api/approvals/pending")
    def get_pending_approvals(request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        pending = engine.get_pending()
        return {
            "count": len(pending),
            "requests": [r.to_dict() for r in pending],
        }

    @app.get("/api/approvals/history")
    def get_approval_history(request: Request, limit: int = 50, offset: int = 0):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        history = engine.get_history(limit=limit, offset=offset)
        return {
            "count": len(history),
            "requests": [r.to_dict() for r in history],
        }

    @app.post("/api/approvals/{request_id}/approve")
    def approve_request(request_id: str, body: ApprovalActionRequest, request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        result = engine.approve(request_id, note=body.note)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Approval request not found or already resolved: {request_id}")
        from jarvis.event_publisher import publish_approval
        publish_approval(request_id, "approved", risk_level=result.risk_level, approved=True)
        return result.to_dict()

    @app.post("/api/approvals/{request_id}/deny")
    def deny_request(request_id: str, body: ApprovalActionRequest, request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        result = engine.deny(request_id, note=body.note)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Approval request not found or already resolved: {request_id}")
        from jarvis.event_publisher import publish_approval
        publish_approval(request_id, "denied", risk_level=result.risk_level, approved=False)
        return result.to_dict()

    @app.get("/api/approvals/policies")
    def get_approval_policies(request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        policies = engine.get_policies()
        return {
            "count": len(policies),
            "policies": [p.to_dict() for p in policies],
        }

    @app.post("/api/approvals/policies")
    def set_approval_policy(body: ApprovalPolicyRequest, request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        policy = engine.set_policy(body.rule_type, body.rule_value, body.enabled)
        return policy.to_dict()

    @app.delete("/api/approvals/policies/{policy_id}")
    def delete_approval_policy(policy_id: int, request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        ok = engine.remove_policy(policy_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Policy not found: {policy_id}")
        return {"deleted": True, "policy_id": policy_id}

    # ══════════════════════════════════════════════════════════════
    # Guardrail Health Dashboard
    # ══════════════════════════════════════════════════════════════

    @app.get("/api/dashboard/guardrail-health")
    def guardrail_health(request: Request, hours: int = 24):
        """Guardrail Health panel data: snapshot + recent events.

        Query params:
            hours: time range filter (1 / 24 / 168 for 7 days). Default 24.
        """
        gt = request.app.extra.get("guardrail_telemetry")
        if gt is None:
            raise HTTPException(status_code=503, detail="Guardrail telemetry not available")

        snap = gt.get_snapshot()
        metrics = snap.get("metrics", {})
        recent = gt.recent_events(50)

        # Apply time-range filter to recent events
        now = time.time()
        cutoff = now - hours * 3600
        filtered_events = [e for e in recent if e.get("timestamp", 0) >= cutoff]

        # Pre-LLM / Post-LLM breakdown for ring charts
        by_type = metrics.get("by_type", {})
        pre_llm = by_type.get("pre_llm", {})
        post_llm = by_type.get("post_llm", {})

        return {
            "pass_count": metrics.get("pass_count", 0),
            "fail_count": metrics.get("fail_count", 0),
            "total_events": metrics.get("total_events", 0),
            "uptime_seconds": metrics.get("uptime_seconds", 0),
            "pre_llm": {
                "blocked": pre_llm.get("blocked", 0),
                "corrected": pre_llm.get("corrected", 0),
                "allowed": pre_llm.get("allowed", 0),
                "total": pre_llm.get("total", 0),
            },
            "post_llm": {
                "blocked": post_llm.get("blocked", 0),
                "corrected": post_llm.get("corrected", 0),
                "allowed": post_llm.get("allowed", 0),
                "total": post_llm.get("total", 0),
            },
            "recent_events": filtered_events,
            "time_range_hours": hours,
        }

    # ══════════════════════════════════════════════════════════════
    # Smart Dashboard Search
    # ══════════════════════════════════════════════════════════════

    @app.get("/api/dashboard/search")
    def dashboard_search(q: str = "", limit: int = 5, request: Request = None):
        """统一搜索：跨 task / eval / audit / healing / context-version 关键词查询"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        query = q.strip().lower()
        results = {
            "query": query,
            "tasks": [],
            "evals": [],
            "audits": [],
            "healing": [],
            "context_versions": [],
            "memories": [],
        }

        if not query:
            return results

        # ── Task history ──
        task_history = getattr(emperor, "task_history", None)
        if task_history is not None:
            for t in getattr(task_history, "_entries", [])[-50:]:
                desc = getattr(t, "task_description", "") or ""
                result = getattr(t, "result", "") or ""
                if isinstance(result, dict):
                    result = str(result)
                text = (desc + " " + result).lower()
                if query in text:
                    results["tasks"].append({
                        "id": getattr(t, "task_id", ""),
                        "description": desc[:120],
                        "status": getattr(t, "status", "unknown"),
                        "minister": getattr(t, "minister", ""),
                    })
            results["tasks"] = results["tasks"][-limit:]

        # ── Eval results ──
        eval_mgr = getattr(emperor, "eval_manager", None)
        if eval_mgr is not None:
            history = getattr(eval_mgr, "_history", [])
            for r in history[-50:]:
                suite = getattr(r, "suite_name", "") or ""
                text = suite.lower()
                if query in text:
                    results["evals"].append({
                        "suite": suite,
                        "passed": getattr(r, "passed", 0),
                        "failed": getattr(r, "failed", 0),
                        "duration_ms": getattr(r, "duration_ms", 0),
                    })
            results["evals"] = results["evals"][-limit:]

        # ── Audit logs ──
        audit_mgr = getattr(emperor, "audit_manager", None)
        if audit_mgr is not None:
            logs = getattr(audit_mgr, "_entries", [])
            for a in logs[-50:]:
                task_desc = getattr(a, "task_description", "") or ""
                result = getattr(a, "result", "") or ""
                text = (task_desc + " " + result).lower()
                if query in text:
                    results["audits"].append({
                        "id": getattr(a, "entry_id", getattr(a, "task_id", "")),
                        "task": task_desc[:120],
                        "result": result[:200],
                        "timestamp": getattr(a, "timestamp", 0),
                    })
            results["audits"] = results["audits"][-limit:]

        # ── Healing history ──
        healer = getattr(emperor, "healing", None)
        if healer is not None:
            for r in healer.history(limit=50):
                text = (r.action_name + " " + r.alert_rule).lower()
                if query in text:
                    results["healing"].append({
                        "action_name": r.action_name,
                        "alert_rule": r.alert_rule,
                        "success": r.success,
                        "error": r.error,
                        "timestamp": r.timestamp,
                    })
            results["healing"] = results["healing"][-limit:]

        # ── Context versions ──
        ctx_mgr = getattr(emperor, "context_versioning", None)
        if ctx_mgr is not None:
            versions = getattr(ctx_mgr, "_versions", [])
            for v in versions[-30:]:
                notes = getattr(v, "notes", "") or ""
                comp = getattr(v, "component", "") or ""
                text = (notes + " " + comp).lower()
                if query in text or query in v.version_tag.lower():
                    results["context_versions"].append({
                        "tag": v.version_tag,
                        "component": comp,
                        "notes": notes[:200],
                        "timestamp": getattr(v, "timestamp", 0),
                    })
            results["context_versions"] = results["context_versions"][-limit:]

        # ── Hierarchical Memories ──
        mem_engine = app.extra.get("hierarchical_memory_engine")
        if mem_engine is not None:
            try:
                _TIER_LAYER = {
                    "WORKING": "L0",
                    "EPISODIC": "L1",
                    "SEMANTIC": "L2",
                    "PROCEDURAL": "L3",
                }
                mem_results = mem_engine.retrieve(query, top_k=limit,
                    tiers=[MemoryTier.WORKING, MemoryTier.EPISODIC, MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL])
                for node in mem_results:
                    tier_name = node.tier.name
                    timestamp = getattr(node, "created_at", 0) or getattr(node, "timestamp", 0)
                    results["memories"].append({
                        "node_id": node.node_id,
                        "content": node.content,
                        "tier": tier_name,
                        "layer": _TIER_LAYER.get(tier_name, tier_name),
                        "importance": round(node.importance, 3),
                        "retention": round(node.decay_retention(), 3),
                        "timestamp": timestamp,
                    })
            except Exception:
                pass  # Non-critical; skip memory search on error

        return results

    @app.get("/api/dashboard/export")
    def dashboard_export(request: Request = None):
        """导出当前 Dashboard 数据为 JSON 快照"""
        emperor = request.app.extra.get("emperor") if request else None
        snap = court.inspect.snapshot() if hasattr(court, 'inspect') else {}

        export_data = {
            "exported_at": int(time.time()),
            "snapshot": {
                "active_ministers": snap.active_count if hasattr(snap, 'active_count') else 0,
                "total_ministers": snap.total_ministers if hasattr(snap, 'total_ministers') else 0,
                "cycle": getattr(court, "cycle", 0),
            },
            "ministers": [],
            "tasks": {"total": 0, "completed": 0, "failed": 0, "success_rate": 0.0},
            "alerts": [],
            "healing": [],
            "config": {},
        }

        # Ministers
        if hasattr(snap, 'ministers'):
            for m in snap.ministers:
                export_data["ministers"].append({
                    "name": m.name, "domain": getattr(m, "domain", "general"),
                    "merit": getattr(m, "merit", 0.0), "status": getattr(m, "status", "unknown"),
                    "tasks_completed": getattr(m, "tasks_completed", 0),
                    "success_rate": getattr(m, "success_rate", 0.0),
                })

        # Tasks
        export_data["tasks"]["total"] = getattr(court, "_total_tasks", 0)
        export_data["tasks"]["completed"] = getattr(court, "_completed_tasks", 0)
        export_data["tasks"]["failed"] = getattr(court, "_failed_tasks", 0)
        export_data["tasks"]["success_rate"] = getattr(court, "success_rate", 0.0)

        # Alerts
        alert_mgr = app.extra.get("alert_manager")
        if alert_mgr:
            export_data["alerts"] = [
                {"rule_name": a.rule_name, "severity": a.severity, "message": a.message,
                 "timestamp": a.timestamp}
                for a in alert_mgr.history(limit=20)
            ]

        # Healing history
        if emperor is not None:
            healer = getattr(emperor, "healing", None)
            if healer:
                export_data["healing"] = [
                    {"action_name": r.action_name, "alert_rule": r.alert_rule,
                     "success": r.success, "timestamp": r.timestamp}
                    for r in healer.history(limit=20)
                ]

        # Config
        export_data["config"] = {
            "min_ministers": getattr(court, "min_ministers", 0),
            "max_ministers": getattr(court, "max_ministers", 0),
            "crossover_rate": getattr(court, "crossover_rate", 0.0),
            "api_port": app.extra.get("port", 9020),
        }

        return export_data

    # ══════════════════════════════════════════════════════════════
    # Self-Healing API
    # ══════════════════════════════════════════════════════════════

    @app.get("/api/healing/actions")
    def healing_actions(request: Request):
        """列出所有自愈动作及状态（冷却、尝试次数、可用）"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        import time as _t
        now = _t.time()
        healer = emperor.healing
        actions = healer.list_actions()

        result = []
        for a in actions:
            last_triggered = healer._last_triggered.get(a.name, 0)
            attempts = healer._attempt_counts.get(a.name, 0)
            cooldown_remaining = max(0, a.cooldown_seconds - (now - last_triggered))
            max_attempts = a.max_attempts if a.max_attempts > 0 else None

            result.append({
                "name": a.name,
                "alert_rule": a.alert_rule,
                "enabled": a.enabled,
                "cooldown_seconds": a.cooldown_seconds,
                "cooldown_remaining": round(cooldown_remaining, 1),
                "attempts": attempts,
                "max_attempts": max_attempts,
                "tags": a.tags,
                "on_cooldown": cooldown_remaining > 0,
                "exhausted": max_attempts is not None and attempts >= max_attempts,
            })

        return {"actions": result, "total": len(result)}

    @app.post("/api/healing/trigger/{action_name}")
    def healing_trigger(action_name: str, request: Request):
        """手动触发指定自愈动作"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        healer = emperor.healing
        action = healer.get_action(action_name)
        if action is None:
            raise HTTPException(status_code=404, detail=f"Action not found: {action_name}")

        # Force trigger bypassing cooldown by calling action directly
        import time as _t
        now = _t.time()
        success = True
        error_msg = ""

        try:
            action.action()
        except Exception as e:
            success = False
            error_msg = str(e)

        # Record the execution
        from jarvis.healing import HealingRecord
        record = HealingRecord(
            action_name=action.name,
            alert_rule=action.alert_rule,
            timestamp=now,
            success=success,
            error=error_msg,
        )
        healer._history.append(record)
        healer._last_triggered[action.name] = now
        healer._attempt_counts[action.name] = healer._attempt_counts.get(action.name, 0) + 1

        # Real-time event
        from jarvis.event_publisher import publish_healing
        publish_healing(action_name, "success" if success else "failure",
                        triggered_by="manual", elapsed_ms=0)

        return {
            "action_name": action_name,
            "success": success,
            "error": error_msg,
            "timestamp": now,
        }

    @app.get("/api/healing/history")
    def healing_history(limit: int = 30, request: Request = None):
        """自愈执行历史"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        healer = emperor.healing
        records = healer.history(limit=limit)

        return {
            "history": [
                {
                    "action_name": r.action_name,
                    "alert_rule": r.alert_rule,
                    "timestamp": r.timestamp,
                    "success": r.success,
                    "error": r.error,
                }
                for r in records
            ],
            "total": len(records),
        }

    @app.get("/api/healing/timeline")
    def healing_timeline(limit: int = 20, request: Request = None):
        """自愈操作时间线 — 返回最近 N 条完整记录"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        healer = emperor.healing
        records = healer.history(limit=limit)

        return {
            "timeline": [
                {
                    "action_name": r.action_name,
                    "result": "success" if r.success else "failed",
                    "triggered_by": r.alert_rule,
                    "elapsed_ms": round(r.recovery_time * 1000, 1) if r.recovery_time else 0,
                    "timestamp": r.timestamp,
                    "error": r.error,
                }
                for r in records
            ],
            "total": len(records),
        }

    @app.post("/api/healing/reset/{action_name}")
    def healing_reset(action_name: str, request: Request):
        """重置自愈动作的尝试计数和冷却"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        healer = emperor.healing
        if action_name == "_all":
            healer.reset_attempts()
        else:
            healer.reset_attempts(action_name)

        return {"ok": True, "action_name": action_name}

    @app.post("/api/healing/toggle/{action_name}")
    def healing_toggle(action_name: str, payload: HealingToggleRequest, request: Request):
        """启用/禁用自愈动作"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        enabled = payload.enabled

        healer = emperor.healing
        action = healer.get_action(action_name)
        if action is None:
            raise HTTPException(status_code=404, detail=f"Action not found: {action_name}")

        # Toggle the actual registered action
        registered = healer._actions.get(action_name)
        if registered:
            registered.enabled = enabled

        return {"action_name": action_name, "enabled": enabled}

    @app.post("/api/healing/check")
    def healing_check(request: Request):
        """执行一轮自愈检查（遍历最近告警）"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        alert_mgr = emperor.alerts
        rule_names = list({a.rule_name for a in alert_mgr.history(limit=50)})
        records = emperor.healing.handle_batch(rule_names)

        return {
            "checked_rules": len(rule_names),
            "actions_executed": len(records),
            "records": [
                {
                    "action_name": r.action_name,
                    "success": r.success,
                    "error": r.error,
                }
                for r in records
            ],
        }

    # ══════════════════════════════════════════════════════════════
    # Reflexion API — self-reflection history & statistics
    # ══════════════════════════════════════════════════════════════

    @app.get("/api/reflexion/history")
    def reflexion_history(limit: int = 50, request: Request = None):
        """获取 Reflexion 自反思历史记录"""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        engine = getattr(emp, "_reflexion_engine", None)
        if engine is None:
            raise HTTPException(status_code=503, detail="ReflexionEngine not available")
        return {
            "history": engine.history(limit=limit),
            "total": min(len(engine._history), limit),
        }

    @app.get("/api/reflexion/stats")
    def reflexion_stats(request: Request):
        """获取 Reflexion 聚合统计信息"""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        engine = getattr(emp, "_reflexion_engine", None)
        if engine is None:
            raise HTTPException(status_code=503, detail="ReflexionEngine not available")
        return engine.stats()

    # ══════════════════════════════════════════════════════════════
    # P0 Governance Agent Endpoints
    # ══════════════════════════════════════════════════════════════

    @app.get("/governance/rules")
    def governance_rules(rule_type: str = "", enabled_only: bool = False):
        """列出所有治理规则，支持按类型和启用状态过滤"""
        gov = app.extra.get("governance_agent")
        if gov is None:
            raise HTTPException(status_code=503, detail="GovernanceAgent not available")
        rules = gov.list_rules(rule_type=rule_type or None, enabled_only=enabled_only)
        return {
            "rules": [r.to_dict() for r in rules],
            "total": len(rules),
        }

    @app.post("/governance/rules")
    def governance_register_rule(req: GovernanceRuleRequest):
        """注册新治理规则"""
        gov = app.extra.get("governance_agent")
        if gov is None:
            raise HTTPException(status_code=503, detail="GovernanceAgent not available")

        if gov.get_rule(req.name) is not None:
            raise HTTPException(status_code=409, detail=f"Rule '{req.name}' already exists")

        # Dynamically compile the check logic
        try:
            check_fn = eval(req.check_logic, {"__builtins__": {}})
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid check logic: {e}")

        rule = GovernanceRule(
            name=req.name,
            rule_type=req.rule_type,
            description=req.description,
            priority=RulePriority[req.priority],
            check_fn=check_fn,
        )

        try:
            gov.register_rule(rule)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

        from jarvis.event_publisher import publish_governance_rule
        publish_governance_rule("create", rule.name, rule.priority.value, rule.description)

        return {"name": rule.name, "rule_type": rule.rule_type, "priority": rule.priority.value}

    @app.delete("/governance/rules/{rule_id}")
    def governance_delete_rule(rule_id: str):
        """删除治理规则"""
        gov = app.extra.get("governance_agent")
        if gov is None:
            raise HTTPException(status_code=503, detail="GovernanceAgent not available")
        if gov.get_rule(rule_id) is None:
            raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
        gov.deregister_rule(rule_id)

        from jarvis.event_publisher import publish_governance_rule
        publish_governance_rule("delete", rule_id)

        return {"ok": True, "rule_id": rule_id}

    @app.put("/governance/rules/{rule_id}/toggle")
    def governance_toggle_rule(rule_id: str, req: GovernanceToggleRequest):
        """启用/禁用治理规则"""
        gov = app.extra.get("governance_agent")
        if gov is None:
            raise HTTPException(status_code=503, detail="GovernanceAgent not available")
        if gov.get_rule(rule_id) is None:
            raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
        if req.enabled:
            gov.enable_rule(rule_id)
        else:
            gov.disable_rule(rule_id)

        from jarvis.event_publisher import publish_governance_rule
        publish_governance_rule("toggle", rule_id, description=("enabled" if req.enabled else "disabled"))

        return {"rule_id": rule_id, "enabled": req.enabled}

    @app.post("/governance/validate")
    def governance_validate(req: GovernanceValidateRequest):
        """校验一个 action，返回 GovernanceResult"""
        gov = app.extra.get("governance_agent")
        if gov is None:
            raise HTTPException(status_code=503, detail="GovernanceAgent not available")
        result = gov.validate(action=req.action, context=req.context)
        return result.to_dict()

    @app.get("/governance/stats")
    def governance_stats():
        """治理统计（通过/阻止/待审批数量）"""
        gov = app.extra.get("governance_agent")
        if gov is None:
            raise HTTPException(status_code=503, detail="GovernanceAgent not available")
        rules = gov.list_rules()
        enabled = sum(1 for r in rules if r.enabled)
        return {
            "total_rules": len(rules),
            "enabled_rules": enabled,
            "disabled_rules": len(rules) - enabled,
            "by_type": {
                "policy": sum(1 for r in rules if r.rule_type == "policy"),
                "rbac": sum(1 for r in rules if r.rule_type == "rbac"),
                "regulatory": sum(1 for r in rules if r.rule_type == "regulatory"),
                "business_logic": sum(1 for r in rules if r.rule_type == "business_logic"),
            },
        }

    # ═══════════════════ Dashboard Governance API ═══════════════════

    @app.get("/api/governance/rules")
    def api_governance_rules():
        """返回所有治理规则列表，供 Dashboard Governance 面板消费"""
        from jarvis.governance_store import governance_store

        rules = governance_store.get_all()
        return {
            "rules": rules,
            "total": len(rules),
        }

    @app.post("/api/governance/rules")
    def api_governance_create_rule(req: GovernanceCreateRequest):
        """创建新治理规则"""
        from jarvis.governance_store import governance_store
        from jarvis.event_publisher import publish_governance_rule

        try:
            rule = governance_store.add(
                description=req.description,
                priority=req.priority,
                remediation=req.remediation,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        publish_governance_rule("create", rule["rule_id"], rule["priority"], rule["description"])

        return {"rule": rule, "message": "规则已创建"}

    @app.delete("/api/governance/rules/{rule_id}")
    def api_governance_delete_rule(rule_id: str):
        """删除治理规则"""
        from jarvis.governance_store import governance_store
        from jarvis.event_publisher import publish_governance_rule

        rule = governance_store.get_by_id(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")

        governance_store.delete(rule_id)
        publish_governance_rule("delete", rule_id, rule["priority"])

        return {"ok": True, "rule_id": rule_id}

    @app.put("/api/governance/rules/{rule_id}/toggle")
    def api_governance_toggle_rule(rule_id: str):
        """切换治理规则启用/禁用状态"""
        from jarvis.governance_store import governance_store
        from jarvis.event_publisher import publish_governance_rule

        rule = governance_store.toggle(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")

        state = "enabled" if rule["enabled"] else "disabled"
        publish_governance_rule("toggle", rule_id, rule["priority"],
                                description=f"Rule {state}")

        return {"rule_id": rule_id, "enabled": rule["enabled"], "rule": rule}

    # ═══════════════════ Dashboard Alert Rules API ═══════════════════

    @app.get("/api/alerts/rules")
    def api_alert_rules():
        """返回所有告警规则列表，供 Dashboard Alert Rules 面板消费"""
        from jarvis.alert_rule_store import alert_rule_store

        rules = alert_rule_store.get_all()
        return {
            "rules": rules,
            "total": len(rules),
        }

    @app.post("/api/alerts/rules")
    def api_alert_create_rule(req: AlertRuleCreateRequest):
        """创建新告警规则"""
        from jarvis.alert_rule_store import alert_rule_store

        try:
            rule = alert_rule_store.add(
                name=req.name,
                condition=req.condition,
                threshold=req.threshold,
                severity=req.severity,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {"rule": rule, "message": "规则已创建"}

    @app.delete("/api/alerts/rules/{rule_id}")
    def api_alert_delete_rule(rule_id: str):
        """删除告警规则"""
        from jarvis.alert_rule_store import alert_rule_store

        rule = alert_rule_store.get_by_id(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")

        alert_rule_store.delete(rule_id)
        return {"ok": True, "rule_id": rule_id}

    @app.put("/api/alerts/rules/{rule_id}/toggle")
    def api_alert_toggle_rule(rule_id: str):
        """切换告警规则启用/禁用状态"""
        from jarvis.alert_rule_store import alert_rule_store

        rule = alert_rule_store.toggle(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")

        return {"rule_id": rule_id, "enabled": rule["enabled"], "rule": rule}

    # ══════════════════════════════════════════════════════════════
    # P0 Bounded Autonomy Endpoints
    # ══════════════════════════════════════════════════════════════

    @app.get("/autonomy/spaces")
    def autonomy_spaces(zone: str = ""):
        """列出所有动作空间定义，按 zone 过滤"""
        engine = app.extra.get("bounded_autonomy_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="BoundedAutonomyEngine not available")

        if zone:
            try:
                zone_enum = ActionZone[zone.upper()]
            except KeyError:
                raise HTTPException(status_code=400, detail=f"Invalid zone: {zone}")
            spaces = engine.list_spaces(zone_enum)
        else:
            spaces = engine.list_spaces()

        return {
            "spaces": [s.to_dict() for s in spaces],
            "total": len(spaces),
        }

    @app.post("/autonomy/spaces")
    def autonomy_register_space(req: AutonomySpaceRequest):
        """注册新动作空间"""
        engine = app.extra.get("bounded_autonomy_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="BoundedAutonomyEngine not available")

        try:
            zone_enum = ActionZone[req.zone.upper()]
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Invalid zone: {req.zone}")

        space = ActionSpace(
            name=req.name,
            zone=zone_enum,
            keywords=set(req.keywords),
            domains=set(req.domains),
            capabilities=set(req.capabilities),
            risk_levels=set(req.risk_levels),
            priority=req.priority,
            description=req.description,
        )
        engine.register_space(space)
        return {"name": space.name, "zone": space.zone.value, "priority": space.priority}

    @app.delete("/autonomy/spaces/{space_id}")
    def autonomy_delete_space(space_id: str):
        """删除动作空间（按名称）"""
        engine = app.extra.get("bounded_autonomy_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="BoundedAutonomyEngine not available")
        removed = engine.deregister_space(space_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Space '{space_id}' not found")
        return {"ok": True, "space_id": space_id}

    @app.post("/autonomy/classify")
    def autonomy_classify(req: AutonomyClassifyRequest):
        """对 action 进行分类，返回 ActionZone"""
        engine = app.extra.get("bounded_autonomy_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="BoundedAutonomyEngine not available")
        zone = engine.classify(action=req.action, context=req.context)
        result = engine.evaluate(action=req.action, context=req.context)
        return {
            "zone": zone.value,
            "can_proceed": result.can_proceed,
            "needs_approval": result.needs_approval,
            "reason": result.reason,
        }

    @app.get("/autonomy/stats")
    def autonomy_stats():
        """三区统计"""
        engine = app.extra.get("bounded_autonomy_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="BoundedAutonomyEngine not available")
        return {
            "green_spaces": len(engine.list_spaces(ActionZone.GREEN)),
            "yellow_spaces": len(engine.list_spaces(ActionZone.YELLOW)),
            "red_spaces": len(engine.list_spaces(ActionZone.RED)),
            "total_spaces": len(engine.list_spaces()),
        }

    # ══════════════════════════════════════════════════════════════
    # P0 Failure Recovery Endpoints
    # ══════════════════════════════════════════════════════════════

    @app.get("/recovery/circuit-breakers")
    def recovery_circuit_breakers():
        """所有熔断器状态"""
        engine = app.extra.get("recovery_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="RecoveryEngine not available")

        cb_stats = engine.circuit_breaker.get_stats()
        return {
            "circuit_breakers": [cb_stats],
            "total": 1,
        }

    @app.post("/recovery/circuit-breakers/{name}/reset")
    def recovery_circuit_breaker_reset(name: str):
        """手动重置熔断器"""
        engine = app.extra.get("recovery_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="RecoveryEngine not available")
        if name != engine.circuit_breaker.name:
            raise HTTPException(status_code=404, detail=f"Circuit breaker '{name}' not found")
        engine.circuit_breaker.reset()
        return {
            "ok": True,
            "name": name,
            "state": engine.circuit_breaker.state.value,
        }

    @app.get("/recovery/stats")
    def recovery_stats():
        """恢复统计（成功/重试/降级/失败）"""
        engine = app.extra.get("recovery_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="RecoveryEngine not available")
        stats = engine.get_stats()
        return stats

    # ════════════════ Tool Guard Endpoints ════════════════════════

    @app.get("/tools/guard/stats")
    def tool_guard_stats():
        """各工具限流状态 + 拦截统计"""
        guard: ToolGuardMiddleware = app.extra.get("tool_guard_middleware")
        if guard is None:
            raise HTTPException(status_code=503, detail="ToolGuardMiddleware not available")
        return guard.get_stats()

    @app.post("/tools/guard/validate-input")
    def tool_guard_validate_input(req: ValidateInputRequest):
        """输入校验测试"""
        guard: ToolGuardMiddleware = app.extra.get("tool_guard_middleware")
        if guard is None:
            raise HTTPException(status_code=503, detail="ToolGuardMiddleware not available")
        result = guard.input_validator.validate(req.input_data)
        return {
            "passed": result.passed,
            "has_critical": result.has_critical,
            "findings": [
                {
                    "severity": f.severity.value,
                    "rule": f.rule,
                    "message": f.message,
                    "location": f.location,
                }
                for f in result.findings
            ],
        }

    @app.post("/tools/guard/filter-output")
    def tool_guard_filter_output(req: FilterOutputRequest):
        """输出过滤测试"""
        guard: ToolGuardMiddleware = app.extra.get("tool_guard_middleware")
        if guard is None:
            raise HTTPException(status_code=503, detail="ToolGuardMiddleware not available")
        result = guard.output_filter.filter(req.output_data, context={"tool_name": req.tool_name})
        return {
            "truncated": result.truncated,
            "original_length": result.original_length,
            "output_length": result.filtered_length,
            "pii_matches": [
                {"type": m.pii_type, "severity": m.severity.value, "match": m.match}
                for m in result.pii_matches
            ],
            "sensitive_keywords_found": result.sensitive_keywords_found,
        }

    @app.post("/tools/guard/rate-limit/reset")
    def tool_guard_rate_limit_reset(req: RateLimitResetRequest):
        """重置限流器"""
        guard: ToolGuardMiddleware = app.extra.get("tool_guard_middleware")
        if guard is None:
            raise HTTPException(status_code=503, detail="ToolGuardMiddleware not available")
        if req.tool_name:
            guard.rate_limiter.reset(req.tool_name)
        else:
            guard.rate_limiter.reset()
        return {"ok": True, "tool_name": req.tool_name or "all"}

    # ═════════════ Hallucination Detector Endpoints ═══════════════

    @app.post("/hallucination/detect")
    def hallucination_detect(req: HallucinationDetectRequest):
        """单样本幻觉检测"""
        detector: HallucinationDetector = app.extra.get("hallucination_detector")
        if detector is None:
            raise HTTPException(status_code=503, detail="HallucinationDetector not available")
        result = detector.detect(req.output, req.context)
        return _hallucination_result_dict(result)

    @app.post("/hallucination/detect/multi")
    def hallucination_detect_multi(req: HallucinationMultiDetectRequest):
        """多样本幻觉检测"""
        detector: HallucinationDetector = app.extra.get("hallucination_detector")
        if detector is None:
            raise HTTPException(status_code=503, detail="HallucinationDetector not available")
        result = detector.detect_multi(req.outputs, req.context)
        return _hallucination_result_dict(result)

    @app.get("/hallucination/stats")
    def hallucination_stats():
        """检测统计"""
        detector: HallucinationDetector = app.extra.get("hallucination_detector")
        if detector is None:
            raise HTTPException(status_code=503, detail="HallucinationDetector not available")
        return detector.get_stats()

    # ═══ Prompt Injection Guard API (P0) ═══════════════════════════

    @app.get("/api/security/scan")
    def security_scan(text: str):
        """手动检测输入/输出文本是否存在 Prompt Injection 风险。

        Query params:
            text: 待检测的文本
        """
        prompt_guard: PromptGuard = app.extra.get("prompt_guard")
        if prompt_guard is None:
            raise HTTPException(status_code=503, detail="PromptGuard not available")
        result = prompt_guard.scan_input(text)
        if result.level == "dangerous":
            return {
                "action": "block",
                "scan_result": result.to_dict(),
            }
        elif result.level == "suspicious":
            return {
                "action": "warn",
                "scan_result": result.to_dict(),
            }
        return {
            "action": "allow",
            "scan_result": result.to_dict(),
        }

    @app.get("/api/security/rules")
    def security_rules():
        """返回当前 PromptGuard 活跃规则列表。"""
        prompt_guard: PromptGuard = app.extra.get("prompt_guard")
        if prompt_guard is None:
            raise HTTPException(status_code=503, detail="PromptGuard not available")
        return {
            "total_rules": len(prompt_guard.list_rules()),
            "rules": prompt_guard.list_rules(),
        }

    # ═══ Hierarchical Memory API ═══════════════════════════════════

    @app.get("/memory/stats")
    def memory_stats():
        """层级记忆统计"""
        engine: HierarchicalMemoryEngine = app.extra.get("hierarchical_memory_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="HierarchicalMemoryEngine not available")
        return engine.stats()

    @app.get("/memory/consolidation-history")
    def memory_consolidation_history(limit: int = 10):
        """合并历史记录"""
        engine: HierarchicalMemoryEngine = app.extra.get("hierarchical_memory_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="HierarchicalMemoryEngine not available")
        return engine.consolidation_history(limit)

    @app.post("/memory/consolidate")
    def memory_consolidate():
        """触发记忆合并周期"""
        engine: HierarchicalMemoryEngine = app.extra.get("hierarchical_memory_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="HierarchicalMemoryEngine not available")
        cycle = engine.consolidate()
        from jarvis.event_publisher import publish_memory
        publish_memory("consolidate", node_count=cycle.working_processed)
        return {
            "cycle_id": cycle.cycle_id,
            "status": cycle.status.value,
            "working_processed": cycle.working_processed,
            "promoted_to_episodic": cycle.promoted_to_episodic,
            "episodic_to_semantic": cycle.episodic_to_semantic,
            "facts_summarized": cycle.facts_summarized,
            "error": cycle.error,
        }

    @app.get("/memory/search")
    def memory_search(q: str = "", top_k: int = 10):
        """语义搜索记忆"""
        engine: HierarchicalMemoryEngine = app.extra.get("hierarchical_memory_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="HierarchicalMemoryEngine not available")
        if not q:
            raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
        results = engine.retrieve(q, top_k=top_k)
        return [r.to_dict() for r in results]

    @app.get("/memory/graph")
    def memory_graph(tier: str = ""):
        """记忆图数据"""
        engine: HierarchicalMemoryEngine = app.extra.get("hierarchical_memory_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="HierarchicalMemoryEngine not available")
        return engine.memory_graph(tier or None)

    @app.post("/memory/add")
    def memory_add(req: MemoryAddRequest):
        """添加记忆节点"""
        engine: HierarchicalMemoryEngine = app.extra.get("hierarchical_memory_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="HierarchicalMemoryEngine not available")
        tier = MemoryTier[req.tier.upper()] if req.tier else MemoryTier.WORKING
        node_id = engine.add(
            content=req.content,
            tier=tier,
            importance=req.importance,
            metadata=req.metadata,
        )
        return {"node_id": node_id, "status": "stored"}

    @app.post("/memory/forget")
    def memory_forget():
        """应用遗忘曲线，清除已遗忘节点"""
        engine: HierarchicalMemoryEngine = app.extra.get("hierarchical_memory_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="HierarchicalMemoryEngine not available")
        purged = engine.apply_forgetting()
        return {"purged": purged, "status": "ok"}

    @app.get("/memory/node/{node_id}")
    def memory_node(node_id: str):
        """获取单个记忆节点详情"""
        engine: HierarchicalMemoryEngine = app.extra.get("hierarchical_memory_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="HierarchicalMemoryEngine not available")
        node = engine.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="Node not found")
        return node

    # ── L4 GraphRAG Memory ─────────────────────────────────────────

    @app.get("/api/memory/graph")
    def memory_graph_rag(query: str = "", top_k: int = 10):
        """GraphRAG 知识图谱检索。

        Query 参数：
        - query: 实体搜索关键词
        - top_k: 返回结果数量（默认 10）
        """
        emperor: Any = app.extra.get("emperor")
        if emperor is None or not hasattr(emperor, "graph_rag"):
            raise HTTPException(status_code=503, detail="GraphRAG engine not available")
        graf = emperor.graph_rag
        results = graf.search(query, top_k=top_k)
        return {
            "query": query,
            "count": len(results),
            "results": [r.to_dict() for r in results],
        }

    @app.get("/api/memory/graph/entity/{name}")
    def memory_graph_entity(name: str):
        """获取指定实体的完整摘要（属性 + 关系）。"""
        emperor: Any = app.extra.get("emperor")
        if emperor is None or not hasattr(emperor, "graph_rag"):
            raise HTTPException(status_code=503, detail="GraphRAG engine not available")
        graf = emperor.graph_rag
        summary = graf.summarize_entity(name)
        fragment = graf.query_graph(name, hops=1)
        return {
            "name": name,
            "summary": summary,
            "fragment": fragment.to_dict(),
        }

    @app.get("/api/memory/graph/entity/{name}/neighbors")
    def memory_graph_neighbors(name: str, relation_type: str = "", hops: int = 1):
        """获取指定实体的邻居（关联实体列表）。"""
        emperor: Any = app.extra.get("emperor")
        if emperor is None or not hasattr(emperor, "graph_rag"):
            raise HTTPException(status_code=503, detail="GraphRAG engine not available")
        graf = emperor.graph_rag
        related = graf.get_related_entities(
            name, relation_type=(relation_type if relation_type else None),
        )
        fragment = graf.query_graph(name, hops=hops)
        return {
            "name": name,
            "neighbors": [e.to_dict() for e in related],
            "neighbor_count": len(related),
            "fragment": fragment.to_dict(),
        }

    @app.get("/api/memory/graph/stats")
    def memory_graph_stats():
        """获取知识图谱统计信息（实体数 / 关系数 / 文档数 / Top 实体）。"""
        emperor: Any = app.extra.get("emperor")
        if emperor is None or not hasattr(emperor, "graph_rag"):
            raise HTTPException(status_code=503, detail="GraphRAG engine not available")
        graf = emperor.graph_rag
        return graf.stats()

    # ── Sandbox Code Runner ───────────────────────────────────────

    import asyncio as _asyncio

    @app.get("/api/dashboard/sandbox/status")
    def sandbox_status(request: Request):
        """Get sandbox manager status."""
        sm: SandboxManager | None = request.app.extra.get("sandbox_manager")
        if sm is None:
            raise HTTPException(status_code=503, detail="Sandbox Manager not available")
        return {
            "engine": sm.engine,
            "timeout_seconds": sm.timeout_seconds,
            "network_enabled": sm.network_enabled,
            "history_count": len(sm.execution_history),
            "available_engines": ["local_subprocess", "local_direct"],
        }

    @app.post("/api/dashboard/sandbox/run")
    async def sandbox_run(payload: SandboxRunRequest, request: Request):
        """Execute Python code in the sandbox."""
        sm: SandboxManager | None = request.app.extra.get("sandbox_manager")
        if sm is None:
            raise HTTPException(status_code=503, detail="Sandbox Manager not available")
        sm.engine = payload.engine
        result = await sm.execute_python(payload.code, timeout=payload.timeout)
        # Real-time event
        from jarvis.event_publisher import publish_sandbox
        publish_sandbox(payload.code, result.exit_code, payload.engine,
                        result.execution_time_ms, result.truncated)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "execution_time_ms": round(result.execution_time_ms, 1),
            "truncated": result.truncated,
        }

    @app.post("/api/dashboard/sandbox/shell")
    async def sandbox_shell(payload: SandboxShellRequest, request: Request):
        """Execute a shell command in the sandbox."""
        sm: SandboxManager | None = request.app.extra.get("sandbox_manager")
        if sm is None:
            raise HTTPException(status_code=503, detail="Sandbox Manager not available")
        result = await sm.execute_shell(payload.command, timeout=payload.timeout)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "execution_time_ms": round(result.execution_time_ms, 1),
            "truncated": result.truncated,
        }

    @app.get("/api/dashboard/sandbox/history")
    def sandbox_history(request: Request, limit: int = 20):
        """Get execution history."""
        sm: SandboxManager | None = request.app.extra.get("sandbox_manager")
        if sm is None:
            raise HTTPException(status_code=503, detail="Sandbox Manager not available")
        history = sm.execution_history[-limit:]
        return {
            "history": list(reversed(history)),
            "total": len(sm.execution_history),
        }

    # ══════════════════════ MCP API Endpoints ═════════════════════

    # Models
    class MCPServerRegister(BaseModel):
        name: str = Field(..., description="MCP Server name")
        transport: str = Field(default="http", description="stdio or http")
        command: str = Field(default="", description="Command for stdio mode")
        args: list[str] = Field(default_factory=list, description="Command args")
        url: str = Field(default="", description="URL for HTTP mode")
        timeout: float = Field(default=30.0, description="Timeout in seconds")

    class MCPToolCall(BaseModel):
        tool_name: str = Field(..., description="Tool name to call")
        arguments: dict = Field(default_factory=dict, description="Tool arguments")

    @app.get("/api/mcp/servers")
    def mcp_list_servers(request: Request):
        """List all registered MCP servers."""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        mgr = emp.mcp_manager
        servers = mgr.list_servers()
        tools_by_server = mgr.get_tools_by_server()
        return {
            "servers": servers,
            "count": len(servers),
            "tools": {
                srv: [{"name": t.name, "description": t.description}
                      for t in tools]
                for srv, tools in tools_by_server.items()
            },
        }

    @app.get("/api/mcp/tools")
    def mcp_list_tools(request: Request):
        """Aggregate all MCP tools from all registered servers."""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        mgr = emp.mcp_manager
        tools = mgr.get_all_tools()
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters_schema": t.parameters_schema,
                }
                for t in tools
            ],
            "count": len(tools),
        }

    @app.post("/api/mcp/call")
    def mcp_call_tool(request: Request, payload: MCPToolCall):
        """Discover and call an MCP tool across all servers."""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        mgr = emp.mcp_manager
        try:
            result = mgr.discover_and_call(payload.tool_name, payload.arguments)
            return {
                "tool_name": payload.tool_name,
                "success": True,
                "result": result.get("result", ""),
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/mcp/servers/register")
    def mcp_register_server(request: Request, payload: MCPServerRegister):
        """Register an external MCP server."""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        mgr = emp.mcp_manager
        from jarvis.mcp_client import MCPServerConfig

        config = MCPServerConfig(
            name=payload.name,
            transport=payload.transport,
            command=payload.command,
            args=payload.args,
            url=payload.url,
            timeout=payload.timeout,
        )
        try:
            mgr.register_server(config)
            return {"message": f"Server '{payload.name}' registered", "success": True}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/mcp/servers/{server_name}")
    def mcp_unregister_server(server_name: str, request: Request):
        """Unregister an MCP server."""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        mgr = emp.mcp_manager
        try:
            mgr.unregister_server(server_name)
            return {"message": f"Server '{server_name}' unregistered", "success": True}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ══════════════════════════════════════════════════════════════════
    # Distributed Tracing API
    # ══════════════════════════════════════════════════════════════════

    from jarvis.tracer import tracer

    @app.get("/api/traces")
    def list_traces(request: Request, limit: int = 20):
        """Recent trace summaries — trace_id, root_span_name, start_time,
        span_count, total_latency_ms."""
        try:
            infos = tracer.list_recent_traces(limit=max(1, min(limit, 100)))
            return {
                "traces": [
                    {
                        "trace_id": t.trace_id,
                        "root_span_name": t.root_span_name,
                        "start_time": t.start_time,
                        "span_count": t.span_count,
                        "total_latency_ms": t.total_latency_ms,
                        "status": t.status,
                    }
                    for t in infos
                ]
            }
        except Exception:
            return {"traces": []}

    @app.get("/api/traces/stats")
    def trace_stats(request: Request):
        """Aggregate trace statistics."""
        try:
            return tracer.stats()
        except Exception:
            return {"total_traces": 0, "avg_latency_ms": 0, "p50_latency_ms": 0,
                    "p95_latency_ms": 0, "p99_latency_ms": 0}

    @app.get("/api/traces/{trace_id}")
    def trace_detail(trace_id: str, request: Request):
        """Full trace detail — all spans with waterfall data."""
        spans = tracer.get_trace(trace_id)
        if not spans:
            raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
        # Find trace-level min start for waterfall alignment
        t_min = min(s.start_time for s in spans)
        return {
            "trace_id": trace_id,
            "spans": [
                {
                    "span_id": s.span_id,
                    "parent_id": s.parent_id,
                    "name": s.name,
                    "kind": s.kind,
                    "start_offset_ms": round((s.start_time - t_min) * 1000, 2),
                    "latency_ms": round(s.latency_ms, 3),
                    "status": s.status,
                    "attributes": s.attributes,
                    "events": [
                        {"name": e.name, "timestamp": e.timestamp, "attributes": e.attributes}
                        for e in s.events
                    ],
                }
                for s in spans
            ],
        }

    # ══════════════════════════════════════════════════════════════════
    # Multi-Model Router API
    # ══════════════════════════════════════════════════════════════════

    @app.get("/api/models")
    def list_models(request: Request, tier: str | None = None):
        """List all registered models with their configuration.

        Query params:
            tier: Optional filter — cheap | standard | premium
        """
        emp = request.app.extra.get("emperor")
        if emp is None:
            multi_router = None
        else:
            multi_router = emp.multi_model_router

        if multi_router is None:
            return {"models": [], "count": 0, "note": "MultiModelRouter not available"}

        models = multi_router.list_models(tier=tier)
        return {
            "models": [m.to_dict() for m in models],
            "count": len(models),
            "tiers": multi_router.get_all_tiers(),
        }

    @app.post("/api/models/benchmark")
    async def benchmark_models(request: Request):
        """Benchmark all registered models on a given prompt.

        Calls each model in parallel and returns latency + output comparison.

        Request body (JSON):
            prompt: str    — the test prompt
            model_ids: list[str] | None — specific models to benchmark (all if omitted)
        """
        import json as _json
        from fastapi import Request as _FR

        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        multi_router = emp.multi_model_router

        # Parse request body manually (FastAPI with async can be tricky here)
        try:
            body = await request.body()
            data = _json.loads(body) if body else {}
        except Exception:
            data = {}

        prompt = data.get("prompt", "")
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")

        model_ids = data.get("model_ids", None)

        messages = [{"role": "user", "content": prompt}]
        results = multi_router.benchmark(messages, model_ids=model_ids)

        return {
            "prompt": prompt,
            "results": [
                {
                    "model_id": r.model_id,
                    "tier": r.tier,
                    "output": r.output,
                    "latency_ms": r.latency_ms,
                    "success": r.success,
                    "error": r.error,
                    "cost_estimate": r.cost_estimate,
                }
                for r in results
            ],
            "fastest": results[0].model_id if results else None,
        }

    # ══════════════════════════════════════════════════════════════════
    # State Machine API
    # ══════════════════════════════════════════════════════════════════

    from jarvis.state_machine import list_workflow_templates, execute_workflow

    @app.get("/api/workflows")
    def list_workflows(request: Request):
        """List all available workflow templates."""
        templates = list_workflow_templates()
        return {"workflows": templates, "count": len(templates)}

    @app.post("/api/workflows/execute")
    def execute_workflow_endpoint(payload: WorkflowExecuteRequest):
        """Execute a named workflow from start to completion.

        Request body (JSON):
            workflow_name: str   — 'dispatch_workflow' or 'error_recovery_workflow'
            data: dict           — initial payload for the workflow
            max_loops: int       — max reflexion loops (dispatch only)
            max_retries: int     — max retry attempts (error_recovery only)
        """
        try:
            result = execute_workflow(
                name=payload.workflow_name,
                initial_data=payload.data,
                max_loops=payload.max_loops,
                max_retries=payload.max_retries,
            )
            return {"success": True, **result}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ══════════════════════════════════════════════════════════════════
    # Handoff Protocol API
    # ══════════════════════════════════════════════════════════════════

    @app.get("/api/handoff/history")
    def handoff_history(request: Request, limit: int = 50):
        """Get recent handoff history (newest first).

        Query params:
            limit: Max entries to return (default 50, max 200)
        """
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        handoff = emp.handoff
        if handoff is None:
            return {"history": [], "count": 0, "note": "HandoffProtocol not initialized"}

        limit = max(1, min(limit, 200))
        history = handoff.get_history(limit=limit)
        return {"history": history, "count": len(history)}

    @app.get("/api/handoff/chain/{task_id}")
    def handoff_chain(request: Request, task_id: str):
        """Get the full handoff chain for a given task ID.

        Returns the ordered list of handoff events for the task,
        from the first ministerial assignment through all subsequent
        handoffs.
        """
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        handoff = emp.handoff
        if handoff is None:
            return {"task_id": task_id, "chain": [], "length": 0,
                    "note": "HandoffProtocol not initialized"}

        chain = handoff.get_chain(task_id)
        return {
            "task_id": task_id,
            "chain": chain,
            "length": len(chain),
        }

    @app.get("/api/handoff/active")
    def handoff_active(request: Request):
        """Get currently in-flight handoffs."""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        handoff = emp.handoff
        if handoff is None:
            return {"active": [], "count": 0,
                    "note": "HandoffProtocol not initialized"}

        active = handoff.get_active_handoffs()
        return {"active": active, "count": len(active)}

    @app.get("/api/handoff/stats")
    def handoff_stats(request: Request):
        """Get handoff statistics (acceptance rate, duration, etc.)."""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        handoff = emp.handoff
        if handoff is None:
            return {"error": "HandoffProtocol not initialized"}

        return handoff.stats()

    @app.get("/api/handoff/targets")
    def handoff_targets(request: Request):
        """List all registered handoff target ministers."""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        handoff = emp.handoff
        if handoff is None:
            return {"targets": [], "count": 0,
                    "note": "HandoffProtocol not initialized"}

        targets = handoff.list_targets()
        return {"targets": targets, "count": len(targets)}

    @app.get("/api/handoff/{handoff_id}")
    def handoff_detail(request: Request, handoff_id: str):
        """Get details of a specific handoff by ID."""
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        handoff = emp.handoff
        if handoff is None:
            return {"handoff_id": handoff_id, "error": "HandoffProtocol not initialized"}

        result = handoff.get_handoff(handoff_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Handoff '{handoff_id}' not found")

        return result

    @app.post("/api/handoff/execute")
    async def handoff_execute(request: Request):
        """Execute a handoff from source to target minister.

        Request body (JSON):
            source_minister: str
            target_minister: str
            task_id: str
            original_prompt: str
            priority: int (1-4, default 2)
            reason: str
            deadline_seconds: float (default 30.0)
            fallback_strategy: str (retry | retry_next | reject | delegate_to_emperor)
            candidate_ministers: list[str] | None
        """
        emp = request.app.extra.get("emperor")
        if emp is None:
            raise HTTPException(status_code=503, detail="Emperor not available")
        handoff = emp.handoff
        if handoff is None:
            raise HTTPException(status_code=503, detail="HandoffProtocol not initialized")

        data = await request.json()
        if data is None:
            raise HTTPException(status_code=400, detail="Request body is required")

        from jarvis.handoff import (
            HandoffRequest, HandoffContext, FallbackStrategy,
        )

        ctx = HandoffContext(
            task_id=data.get("task_id", ""),
            original_prompt=data.get("original_prompt", ""),
            priority=data.get("priority", 2),
        )

        fallback_str = data.get("fallback_strategy", "reject")
        try:
            fallback = FallbackStrategy(fallback_str)
        except ValueError:
            fallback = FallbackStrategy.REJECT

        req = HandoffRequest(
            source_minister=data.get("source_minister", ""),
            target_minister=data.get("target_minister", ""),
            context=ctx,
            reason=data.get("reason", ""),
            priority=data.get("priority", 2),
            deadline_seconds=data.get("deadline_seconds", 30.0),
            fallback_strategy=fallback,
            candidate_ministers=data.get("candidate_ministers", []),
        )

        result = handoff.handoff(req)
        return result.to_dict()

    # ══════════════════════════════════════════════════════════════════
    # RBAC endpoints
    # ══════════════════════════════════════════════════════════════════

    @app.get("/api/rbac/roles")
    def rbac_list_roles(request: Request):
        """List all roles with their permissions and priority."""
        rbac = request.app.extra.get("rbac_engine")
        if rbac is None:
            raise HTTPException(status_code=503, detail="RBAC engine not available")
        return {"roles": rbac.list_roles()}

    @app.post("/api/rbac/roles")
    async def rbac_create_role(request: Request):
        """Create a new custom role.

        Request body (JSON):
            name: str — unique role name
            permissions: list[str] — permission names (e.g. ["file_read", "model_call"])
            priority: int (optional, default 50)
        """
        rbac = request.app.extra.get("rbac_engine")
        if rbac is None:
            raise HTTPException(status_code=503, detail="RBAC engine not available")

        data = await request.json()
        if data is None:
            raise HTTPException(status_code=400, detail="Request body is required")

        name = data.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Role 'name' is required")

        perms: set[Permission] = set()
        for label in data.get("permissions", []):
            p = Permission.from_label(label)
            if p is None:
                raise HTTPException(status_code=400, detail=f"Unknown permission: '{label}'")
            perms.add(p)

        priority = data.get("priority", 50)
        try:
            role = rbac.create_role(name, permissions=perms, priority=priority)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

        return {
            "message": f"Role '{name}' created",
            "role": role.to_dict(),
        }

    @app.post("/api/rbac/grant")
    async def rbac_grant_permission(request: Request):
        """Grant a permission to a role.

        Request body (JSON):
            role_name: str — target role name
            permission: str — permission label (e.g. "shell_exec")
        """
        rbac = request.app.extra.get("rbac_engine")
        if rbac is None:
            raise HTTPException(status_code=503, detail="RBAC engine not available")

        data = await request.json()
        if data is None:
            raise HTTPException(status_code=400, detail="Request body is required")

        role_name = data.get("role_name", "").strip()
        perm_label = data.get("permission", "").strip()

        if not role_name:
            raise HTTPException(status_code=400, detail="'role_name' is required")
        if not perm_label:
            raise HTTPException(status_code=400, detail="'permission' is required")

        perm = Permission.from_label(perm_label)
        if perm is None:
            raise HTTPException(status_code=400, detail=f"Unknown permission: '{perm_label}'")

        try:
            rbac.grant(role_name, perm)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=404, detail=str(e))

        return {
            "message": f"Permission '{perm_label}' granted to role '{role_name}'",
            "role": rbac.get_role_detail(role_name),
        }

    @app.post("/api/rbac/revoke")
    async def rbac_revoke_permission(request: Request):
        """Revoke a permission from a role.

        Request body (JSON):
            role_name: str — target role name
            permission: str — permission label (e.g. "shell_exec")
        """
        rbac = request.app.extra.get("rbac_engine")
        if rbac is None:
            raise HTTPException(status_code=503, detail="RBAC engine not available")

        data = await request.json()
        if data is None:
            raise HTTPException(status_code=400, detail="Request body is required")

        role_name = data.get("role_name", "").strip()
        perm_label = data.get("permission", "").strip()

        if not role_name:
            raise HTTPException(status_code=400, detail="'role_name' is required")
        if not perm_label:
            raise HTTPException(status_code=400, detail="'permission' is required")

        perm = Permission.from_label(perm_label)
        if perm is None:
            raise HTTPException(status_code=400, detail=f"Unknown permission: '{perm_label}'")

        try:
            rbac.revoke(role_name, perm)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=404, detail=str(e))

        return {
            "message": f"Permission '{perm_label}' revoked from role '{role_name}'",
            "role": rbac.get_role_detail(role_name),
        }

    # ══════════════════════════════════════════════════════════════
    # Cost Tracking API
    # ══════════════════════════════════════════════════════════════

    @app.get("/api/costs/summary")
    def cost_summary(request: Request):
        """返回今日/本月/总计成本摘要。"""
        tracker = request.app.extra.get("cost_tracker")
        if tracker is None:
            raise HTTPException(status_code=503, detail="CostTracker not available")
        return tracker.summary()

    @app.get("/api/costs/history")
    def cost_history(limit: int = 50, request: Request = None):
        """返回最近 N 条成本调用记录。"""
        tracker = request.app.extra.get("cost_tracker")
        if tracker is None:
            raise HTTPException(status_code=503, detail="CostTracker not available")
        return {
            "records": tracker.history(limit=min(limit, 200)),
            "total": len(tracker._records_snapshot()),
        }

    @app.get("/api/costs/by-model")
    def cost_by_model(request: Request):
        """返回按模型分组的成本明细（本月）。"""
        tracker = request.app.extra.get("cost_tracker")
        if tracker is None:
            raise HTTPException(status_code=503, detail="CostTracker not available")
        return {
            "today": tracker.per_model_breakdown(since=tracker._today_start()),
            "this_month": tracker.per_model_breakdown(since=tracker._month_start()),
            "all_time": tracker.per_model_breakdown(since=0),
        }

    # ── Cost Efficiency API ────────────────────────────────────────

    @app.get("/api/dashboard/cost-efficiency")
    def cost_efficiency(
        hours: int = 0,
        trend_bucket: str = "day",
        request: Request = None,
    ):
        """Return cost-efficiency metrics: CPSR, success rate, trends."""
        tracker = request.app.extra.get("cost_per_success")
        if tracker is None:
            raise HTTPException(
                status_code=503, detail="CostPerSuccessTracker not available"
            )
        return tracker.get_report(format="json", hours=hours, trend_bucket=trend_bucket)

    return app


# ══════════════════════════════════════════════════════════════════
# Default instance
# ══════════════════════════════════════════════════════════════════

app: FastAPI = create_app()


# ══════════════════════════════════════════════════════════════════
# CLI entry: python -m jarvis.court_api
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Emperor Court API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    config = None
    if args.config:
        cfg_path = args.config.resolve()
        if not cfg_path.exists():
            raise SystemExit(f"Config file not found: {cfg_path}")
        config = SurvivalConfig.from_yaml(str(cfg_path))
        print(f"Loaded config: {cfg_path}")

    server_app = create_app(config=config)
    print(f"Emperor Court API -> http://{args.host}:{args.port}")
    uvicorn.run(server_app, host=args.host, port=args.port,
                reload=args.reload, log_level="info")
