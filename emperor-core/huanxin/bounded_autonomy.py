"""
Bounded Autonomy — three-zone action space for safe AI autonomy.

Implements the "three-zone model" where every agent action is classified
into one of three autonomy zones:
    GREEN  → auto-execute (safe, low-risk operations)
    YELLOW → requires human approval before execution
    RED    → forbidden / requires explicit override

Inspired by the 2026 AI safety consensus: agents should have bounded
autonomy with graduated levels of human oversight, never unlimited access.

Architecture:
    ActionZone           — enum: GREEN (auto) / YELLOW (approval) / RED (blocked)
    ActionSpace          — zone definition with match conditions
    BoundedAutonomyEngine — classifier + space registry + governance integration

Integration:
    - huanxin.governance_agent.GovernanceAgent → RED actions go through governance first
    - huanxin.approval.ApprovalEngine → YELLOW actions auto-trigger approval flow

Built-in Default Spaces (aligned with approval.py risk classification):
    GREEN:   read-only queries, ticket creation, information retrieval
    YELLOW:  data modification, notifications, configuration changes
    RED:     file deletion, refunds/payments, critical system operations

Usage:
    from huanxin.bounded_autonomy import (
        BoundedAutonomyEngine, ActionSpace, ActionZone,
    )

    engine = BoundedAutonomyEngine(
        governance_agent=gov,
        approval_engine=approval,
    )

    zone = engine.classify({"tool": "delete", "args": {...}})
    if zone == ActionZone.GREEN:
        execute(action)
    elif zone == ActionZone.YELLOW:
        show_approval_ui(action)
    else:  # RED
        block_and_alert(action)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("huanxin.bounded_autonomy")


# ══════════════════════════════════════════════════════════════════
# ActionZone Enum
# ══════════════════════════════════════════════════════════════════


class ActionZone(str, Enum):
    """Three-zone autonomy classification.

    GREEN:  Safe, low-risk — execute automatically without human approval.
    YELLOW: Moderate risk — requires human approval before execution.
    RED:    High risk — forbidden unless explicitly overridden.
    """
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

    def __repr__(self) -> str:
        return f"ActionZone.{self.name}"


# ══════════════════════════════════════════════════════════════════
# ActionSpace
# ══════════════════════════════════════════════════════════════════


@dataclass
class ActionSpace:
    """Definition of an action space — what zone an action belongs to.

    An ActionSpace defines matching conditions (domain, risk_level, capability,
    keywords) that determine which zone a given action should be assigned to.

    Attributes:
        name: Unique name for this space (e.g. "read-only-queries")
        zone: The ActionZone (GREEN / YELLOW / RED)
        domains: Set of domains this space applies to; empty = all domains
        risk_levels: Set of risk levels; empty = all levels
        capabilities: Set of capability names; empty = all capabilities
        keywords: Keywords to match in action prompt / description
        priority: Higher number = checked first (for conflict resolution)
        description: Human-readable description
        custom_matcher: Optional custom match function (action, context) -> bool
    """
    name: str
    zone: ActionZone = ActionZone.GREEN
    domains: Set[str] = field(default_factory=set)
    risk_levels: Set[str] = field(default_factory=set)
    capabilities: Set[str] = field(default_factory=set)
    keywords: Set[str] = field(default_factory=set)
    priority: int = 0
    description: str = ""
    custom_matcher: Optional[Callable[[Any, Dict[str, Any]], bool]] = None

    def matches(
        self,
        action: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if this action space matches the given action and context.

        All specified conditions must match (AND logic). Empty sets = match-all.
        """
        ctx = context or {}

        # Domain match
        if self.domains:
            domain = ctx.get("domain", "")
            if domain not in self.domains:
                return False

        # Risk level match
        if self.risk_levels:
            risk = ctx.get("risk_level", "")
            if risk not in self.risk_levels:
                return False

        # Capability match
        if self.capabilities and isinstance(action, dict):
            cap = action.get("capability", action.get("tool", ""))
            if cap not in self.capabilities:
                return False

        # Keyword match
        if self.keywords:
            action_str = self._action_to_string(action)
            if action_str:
                # case-insensitive keyword match
                action_lower = action_str.lower()
                if not any(kw.lower() in action_lower for kw in self.keywords):
                    return False
            else:
                return False

        # Custom matcher
        if self.custom_matcher:
            return self.custom_matcher(action, ctx)

        return True

    @staticmethod
    def _action_to_string(action: Any) -> str:
        """Convert action to a searchable string."""
        if isinstance(action, dict):
            parts = []
            for k in ("tool", "action", "method", "operation", "prompt"):
                if k in action:
                    parts.append(str(action[k]))
            if "args" in action and isinstance(action["args"], dict):
                for v in action["args"].values():
                    parts.append(str(v))
            return " ".join(parts)
        return str(action)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "zone": self.zone.value,
            "domains": sorted(self.domains),
            "risk_levels": sorted(self.risk_levels),
            "capabilities": sorted(self.capabilities),
            "keywords": sorted(self.keywords),
            "priority": self.priority,
            "description": self.description,
        }


# ══════════════════════════════════════════════════════════════════
# BoundedAutonomyEngine
# ══════════════════════════════════════════════════════════════════


class BoundedAutonomyEngine:
    """Three-zone action space classifier for bounded AI autonomy.

    Classification Pipeline:
        1. Check all registered ActionSpaces (priority descending)
        2. First match wins → return matched zone
        3. No match → default to YELLOW (fail-safe)
        4. YELLOW auto-triggers approval flow via ApprovalEngine
        5. RED actions route through GovernanceAgent for additional validation

    Built-in Default Spaces (reference: huanxin.approval risk keywords):
        GREEN:  read, query, search, list, get, info, status, create ticket/task
        YELLOW: modify, update, write, send, notify, configure, install
        RED:    delete, remove, format, wipe, refund, payment, shutdown
    """

    # ── Built-in default spaces ──

    DEFAULT_GREEN_SPACES: List[ActionSpace] = [
        ActionSpace(
            name="read-only-queries",
            zone=ActionZone.GREEN,
            keywords={"read", "query", "search", "list", "get", "status",
                      "info", "view", "show", "find", "lookup", "check",
                      "查询", "读取", "搜索", "查看", "列出", "状态", "检索",
                      "fetch", "describe", "summary", "统计"},
            priority=100,
            description="Read-only data queries and information retrieval",
        ),
        ActionSpace(
            name="ticket-creation",
            zone=ActionZone.GREEN,
            keywords={"create ticket", "新建工单", "create task", "创建任务",
                      "raise", "submit", "report issue"},
            priority=90,
            description="Safe creation of new tickets and tasks",
        ),
        ActionSpace(
            name="safe-api-calls",
            zone=ActionZone.GREEN,
            domains={"weather", "news", "search", "translate"},
            priority=80,
            description="External API calls to safe, read-only domains",
        ),
    ]

    DEFAULT_YELLOW_SPACES: List[ActionSpace] = [
        ActionSpace(
            name="data-modification",
            zone=ActionZone.YELLOW,
            keywords={"modify", "update", "write", "insert", "upsert",
                      "修改", "更新", "写入", "覆盖", "replace", "edit",
                      "变更", "调整", "alter", "补丁", "patch"},
            domains={"database", "storage", "config"},
            priority=100,
            description="Data modifications requiring approval",
        ),
        ActionSpace(
            name="notifications",
            zone=ActionZone.YELLOW,
            keywords={"send", "notify", "push", "email", "message",
                      "发送", "通知", "推送", "邮件", "消息", "短信",
                      "announce", "broadcast"},
            priority=90,
            description="Outbound notifications and messages",
        ),
        ActionSpace(
            name="configuration-changes",
            zone=ActionZone.YELLOW,
            keywords={"configure", "config", "setting", "settings",
                      "配置", "设置", "环境变量", "env", "parameter",
                      "install", "安装", "deploy", "部署", "upgrade"},
            domains={"config", "deployment", "infrastructure"},
            priority=85,
            description="Configuration and environment changes",
        ),
        ActionSpace(
            name="file-operations-safe",
            zone=ActionZone.YELLOW,
            keywords={"move", "移动", "复制", "copy", "重命名", "rename",
                      "压缩", "compress", "解压", "extract", "转换", "convert"},
            priority=80,
            description="Non-destructive file operations",
        ),
    ]

    DEFAULT_RED_SPACES: List[ActionSpace] = [
        ActionSpace(
            name="destructive-operations",
            zone=ActionZone.RED,
            keywords={"delete", "remove", "format", "wipe", "destroy",
                      "删除", "移除", "格式化", "清空", "销毁",
                      "rm -rf", "drop table", "truncate"},
            priority=100,
            description="Destructive operations — forbidden without explicit override",
        ),
        ActionSpace(
            name="payments-refunds",
            zone=ActionZone.RED,
            keywords={"refund", "payment", "charge", "bill", "invoice",
                      "退款", "付款", "支付", "转账", "扣款",
                      "purchase", "transaction", "withdraw"},
            domains={"finance", "payment", "billing"},
            priority=100,
            description="Financial transactions requiring explicit authorization",
        ),
        ActionSpace(
            name="critical-system-ops",
            zone=ActionZone.RED,
            keywords={"shutdown", "reboot", "restart", "kill",
                      "关闭", "重启", "停机", "杀死", "终止",
                      "registry", "注册表", "regedit", "sudo", "root",
                      "chmod 777", "systemctl", "service stop"},
            domains={"system", "registry", "kernel"},
            priority=95,
            description="Critical system-level operations",
        ),
        ActionSpace(
            name="auth-credential-ops",
            zone=ActionZone.RED,
            keywords={"password", "token", "secret", "api key", "credential",
                      "密码", "令牌", "密钥", "凭证", "私钥",
                      "auth", "login", "sudo", "su"},
            domains={"auth", "security", "identity"},
            priority=90,
            description="Authentication and credential operations",
        ),
    ]

    # ── Engine ──

    def __init__(
        self,
        governance_agent: Any = None,
        approval_engine: Any = None,
        default_zone: ActionZone = ActionZone.YELLOW,
        load_defaults: bool = True,
    ):
        """Initialize BoundedAutonomyEngine.

        Args:
            governance_agent: huanxin.governance_agent.GovernanceAgent for RED validation
            approval_engine: huanxin.approval.ApprovalEngine for YELLOW approval
            default_zone: fallback zone when no space matches (default YELLOW for safety)
            load_defaults: whether to register built-in default spaces
        """
        self._spaces: Dict[str, ActionSpace] = {}
        self._governance_agent = governance_agent
        self._approval_engine = approval_engine
        self.default_zone = default_zone

        if load_defaults:
            self._register_defaults()

    def _register_defaults(self) -> None:
        """Register all built-in default action spaces."""
        for space in (
            self.DEFAULT_GREEN_SPACES
            + self.DEFAULT_YELLOW_SPACES
            + self.DEFAULT_RED_SPACES
        ):
            self.register_space(space)

    # ── Space Management ──

    def register_space(self, space: ActionSpace) -> None:
        """Register a custom action space (or overwrite by name)."""
        if not space.name:
            raise ValueError("ActionSpace must have a non-empty name")
        self._spaces[space.name] = space
        logger.debug("ActionSpace registered: %s → %s (priority=%d)",
                     space.name, space.zone.value, space.priority)

    def deregister_space(self, name: str) -> bool:
        """Remove a registered space by name."""
        if name in self._spaces:
            del self._spaces[name]
            return True
        return False

    def get_space(self, name: str) -> Optional[ActionSpace]:
        return self._spaces.get(name)

    def list_spaces(self, zone: Optional[ActionZone] = None) -> List[ActionSpace]:
        """List all registered spaces, optionally filtered by zone."""
        spaces = list(self._spaces.values())
        if zone:
            spaces = [s for s in spaces if s.zone == zone]
        return sorted(spaces, key=lambda s: (-s.priority, s.name))

    # ── Classification ──

    def classify(
        self,
        action: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> ActionZone:
        """Classify an action into GREEN / YELLOW / RED.

        Args:
            action: The action to classify (dict or structured representation)
            context: Additional context (domain, risk_level, capability, etc.)

        Returns:
            ActionZone — the determined autonomy zone.

        Classification Logic:
            1. Sort all registered spaces by priority descending
            2. First matching space wins → return its zone
            3. No match → return default_zone (YELLOW for safety)
        """
        ctx = context or {}

        # Sort spaces by priority descending
        sorted_spaces = sorted(
            self._spaces.values(),
            key=lambda s: -s.priority,
        )

        for space in sorted_spaces:
            if space.matches(action, ctx):
                logger.info(
                    "Action classified: zone=%s space=%s domain=%s capability=%s",
                    space.zone.value, space.name,
                    ctx.get("domain", ""), ctx.get("capability", ""),
                )
                return space.zone

        # No match → default zone (safety: YELLOW)
        logger.info(
            "Action classified: zone=%s (default, no matching space) "
            "domain=%s capability=%s",
            self.default_zone.value, ctx.get("domain", ""), ctx.get("capability", ""),
        )
        return self.default_zone

    # ── Pipeline: classify + enforce ──

    def evaluate(
        self,
        action: Any,
        context: Optional[Dict[str, Any]] = None,
        task_id: str = "",
    ) -> BoundedAutonomyResult:
        """Evaluate an action — classify zone and enforce governance.

        This is the main entry point. It combines:
            - Zone classification
            - GREEN → approved for auto-execution
            - YELLOW → triggers approval via ApprovalEngine if available
            - RED → routes through GovernanceAgent for additional validation

        Args:
            action: Action to evaluate
            context: Context dict (domain, risk_level, capability, etc.)
            task_id: Optional task ID for trace linkage

        Returns:
            BoundedAutonomyResult with zone, can_proceed, and details.
        """
        import time as _time
        t0 = _time.perf_counter()
        ctx = context or {}

        zone = self.classify(action, ctx)

        result = BoundedAutonomyResult(
            zone=zone,
            task_id=task_id,
        )

        if zone == ActionZone.GREEN:
            result.can_proceed = True
            result.reason = "GREEN zone — auto-execution approved."

        elif zone == ActionZone.YELLOW:
            result.can_proceed = False
            result.needs_approval = True

            if self._approval_engine is not None:
                try:
                    from huanxin.approval import classify_risk
                    action_summary = self._summarize_action(action)
                    domain = ctx.get("domain", "general")
                    capability = ctx.get("capability", "")
                    risk_level = classify_risk(action_summary, domain)

                    req = self._approval_engine.create_request(
                        task_id=task_id or "unknown",
                        prompt=f"[BoundedAutonomy|YELLOW] {action_summary}",
                        domain=domain,
                        capability=capability,
                        extra={
                            "zone": "yellow",
                            "risk_level": risk_level,
                            "context": ctx,
                        },
                    )
                    result.approval_request_id = req.id
                    result.reason = (
                        f"YELLOW zone — requires human approval. "
                        f"Approval request: {req.id}"
                    )
                except Exception as exc:
                    logger.error("Failed to create approval for YELLOW action: %s", exc)
                    result.can_proceed = False
                    result.reason = (
                        f"YELLOW zone — approval engine unavailable: {exc}"
                    )
            else:
                result.reason = "YELLOW zone — requires human approval (no engine configured)."

        else:  # RED
            result.can_proceed = False

            # Route through GovernanceAgent if available
            if self._governance_agent is not None:
                try:
                    gov_result = self._governance_agent.validate(
                        action=action,
                        context=ctx,
                        task_id=task_id,
                    )
                    result.governance_result = gov_result
                    if gov_result.passed:
                        result.reason = "RED zone — governance check passed, but explicit override still required."
                    elif gov_result.needs_approval:
                        result.needs_approval = True
                        result.approval_request_id = gov_result.approval_request_id
                        result.reason = (
                            f"RED zone — governance requires approval: {gov_result.reason}"
                        )
                    else:
                        result.reason = (
                            f"RED zone — blocked by governance: {gov_result.reason}"
                        )
                except Exception as exc:
                    logger.error("Governance validation failed for RED action: %s", exc)
                    result.reason = f"RED zone — governance validation error: {exc}"
            else:
                result.reason = "RED zone — forbidden without explicit override."

        result.duration_ms = (_time.perf_counter() - t0) * 1000
        return result

    @staticmethod
    def _summarize_action(action: Any) -> str:
        """Create a short summary of the action."""
        if isinstance(action, dict):
            tool = action.get("tool", action.get("action", ""))
            target = action.get("target", action.get("path", action.get("prompt", "")))
            return f"{tool}: {str(target)[:150]}"
        return str(action)[:200]


# ══════════════════════════════════════════════════════════════════
# BoundedAutonomyResult
# ══════════════════════════════════════════════════════════════════


@dataclass
class BoundedAutonomyResult:
    """Result of bounded autonomy evaluation.

    Attributes:
        zone: Determined ActionZone
        can_proceed: Whether auto-execution is allowed
        needs_approval: Whether human approval is required
        approval_request_id: ID from ApprovalEngine (if approval created)
        governance_result: Result from GovernanceAgent (if RED + gov enabled)
        reason: Human-readable explanation
        task_id: Associated task ID
        duration_ms: Evaluation duration
    """
    zone: ActionZone = ActionZone.YELLOW
    can_proceed: bool = False
    needs_approval: bool = False
    approval_request_id: Optional[str] = None
    governance_result: Any = None
    reason: str = ""
    task_id: str = ""
    duration_ms: float = 0

    def to_dict(self) -> dict:
        return {
            "zone": self.zone.value,
            "can_proceed": self.can_proceed,
            "needs_approval": self.needs_approval,
            "approval_request_id": self.approval_request_id,
            "reason": self.reason,
            "task_id": self.task_id,
            "duration_ms": self.duration_ms,
        }
