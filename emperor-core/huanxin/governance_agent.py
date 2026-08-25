"""
Governance Agent — "Agent that monitors Agents".

Production-grade governance layer that sits between Agent output and execution,
enforcing policy compliance, RBAC, regulatory rules, and business logic before
any action is committed.

Inspired by 2026 AI safety best practices: every autonomous action must
pass through a governance gate with immutable audit trail.

Architecture:
    GovernanceRule     — single governance rule (4 types)
    GovernanceResult   — validation outcome (passed / blocked / needs_approval)
    GovernanceAgent    — rule engine + ApprovalEngine integration + audit

Rule Types:
    policy_compliance  — policy / compliance check (e.g. "no PII in output")
    rbac               — role-based access control
    regulatory         — industry regulation (GDPR, SOX, HIPAA, etc.)
    business_logic     — custom domain-specific validation

Priority Levels:
    CRITICAL (4) > HIGH (3) > MEDIUM (2) > LOW (1)

Integration:
    - huanxin.audit.AuditLogger   → immutable audit trail per decision
    - huanxin.approval.ApprovalEngine → auto-create approval for needs_approval

Usage:
    from huanxin.governance_agent import GovernanceAgent, GovernanceRule

    gov = GovernanceAgent(audit_logger=audit, approval_engine=approval)

    gov.register_rule(GovernanceRule(
        name="no-pii-output",
        rule_type="policy_compliance",
        priority=4,  # CRITICAL
        check_fn=lambda action, ctx: "ssn" not in str(action).lower(),
        description="Block actions containing PII patterns",
    ))

    result = gov.validate(action={"tool": "delete", "target": "..."}, context={})
    if result.passed:
        execute(action)
    elif result.needs_approval:
        show_approval_ui(result)
    else:
        block_action(result)
"""

from __future__ import annotations

import logging
import uuid
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("huanxin.governance_agent")


# ══════════════════════════════════════════════════════════════════
# Enums & Constants
# ══════════════════════════════════════════════════════════════════


class RulePriority(IntEnum):
    """Governance rule priority levels (higher = more critical)."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def from_string(cls, s: str) -> "RulePriority":
        mapping = {
            "critical": cls.CRITICAL,
            "high": cls.HIGH,
            "medium": cls.MEDIUM,
            "low": cls.LOW,
        }
        return mapping.get(s.lower(), cls.MEDIUM)


class GovernanceStatus:
    """Result status constants."""
    PASSED = "passed"
    BLOCKED = "blocked"
    NEEDS_APPROVAL = "needs_approval"


RULE_TYPES = ("policy_compliance", "rbac", "regulatory", "business_logic")

DEFAULT_AUDIT_PHASE = "governance"


# ══════════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════════


@dataclass
class GovernanceRule:
    """A single governance rule with metadata and check function.

    Attributes:
        name: Unique rule name (e.g. "no-pii-output", "rbac-admin-only")
        rule_type: One of policy_compliance / rbac / regulatory / business_logic
        priority: CRITICAL(4) > HIGH(3) > MEDIUM(2) > LOW(1)
        check_fn: Callable(action, context) -> bool; True = pass, False = fail
        description: Human-readable rule description
        enabled: Whether the rule is active
        metadata: Arbitrary extra data (tags, ownership, etc.)
    """
    name: str
    rule_type: str = "policy_compliance"
    priority: RulePriority = RulePriority.MEDIUM
    check_fn: Optional[Callable[[Any, Dict[str, Any]], bool]] = None
    description: str = ""
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.rule_type not in RULE_TYPES:
            raise ValueError(f"rule_type must be one of {RULE_TYPES}, got '{self.rule_type}'")
        if isinstance(self.priority, int):
            self.priority = RulePriority(self.priority)
        elif isinstance(self.priority, str):
            self.priority = RulePriority.from_string(self.priority)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rule_type": self.rule_type,
            "priority": int(self.priority),
            "priority_name": self.priority.name,
            "description": self.description,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }


@dataclass
class GovernanceResult:
    """Outcome of a governance validation.

    Attributes:
        passed: Validation passed, action can proceed
        blocked: Validation blocked, action must NOT proceed
        needs_approval: Action requires human approval before execution
        matched_rules: List of rules that triggered
        failed_rules: List of rules that failed (for blocked results)
        reason: Human-readable explanation
        trace_id: Unique trace for audit linkage
        approval_request_id: ID from ApprovalEngine if needs_approval triggered
        duration_ms: Validation duration
    """
    status: str = GovernanceStatus.PASSED  # passed | blocked | needs_approval
    matched_rules: List[str] = field(default_factory=list)
    failed_rules: List[str] = field(default_factory=list)
    reason: str = ""
    trace_id: str = ""
    approval_request_id: Optional[str] = None
    duration_ms: float = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == GovernanceStatus.PASSED

    @property
    def blocked(self) -> bool:
        return self.status == GovernanceStatus.BLOCKED

    @property
    def needs_approval(self) -> bool:
        return self.status == GovernanceStatus.NEEDS_APPROVAL

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "passed": self.passed,
            "blocked": self.blocked,
            "needs_approval": self.needs_approval,
            "matched_rules": self.matched_rules,
            "failed_rules": self.failed_rules,
            "reason": self.reason,
            "trace_id": self.trace_id,
            "approval_request_id": self.approval_request_id,
            "duration_ms": self.duration_ms,
        }


# ══════════════════════════════════════════════════════════════════
# GovernanceAgent
# ══════════════════════════════════════════════════════════════════


class GovernanceAgent:
    """Central governance engine — validates Agent actions before execution.

    Responsibilities:
        1. Register / deregister / enable / disable governance rules
        2. Validate actions against all enabled rules (priority-ordered)
        3. Auto-create ApprovalEngine requests for needs_approval scenarios
        4. Write immutable audit trail for every governance decision

    Rule Execution Order:
        Rules are sorted by priority descending (CRITICAL first).
        If any rule blocks, validation stops and returns BLOCKED.
        If no rule blocks and some flag needs_approval, returns NEEDS_APPROVAL.
        Otherwise returns PASSED.
    """

    def __init__(
        self,
        audit_logger: Any = None,
        approval_engine: Any = None,
        task_id_prefix: str = "gov",
    ):
        """Initialize GovernanceAgent.

        Args:
            audit_logger: huanxin.audit.AuditLogger instance for immutable audit trail
            approval_engine: huanxin.approval.ApprovalEngine instance for HITL approval
            task_id_prefix: prefix for auto-generated task IDs in approval requests
        """
        self._rules: Dict[str, GovernanceRule] = {}
        self._audit_logger = audit_logger
        self._approval_engine = approval_engine
        self._task_id_prefix = task_id_prefix

    # ── Rule Management ──

    def register_rule(self, rule: GovernanceRule) -> None:
        """Register a new governance rule (or overwrite existing by name)."""
        if not rule.name:
            raise ValueError("Rule must have a non-empty name")
        self._rules[rule.name] = rule
        logger.info("Governance rule registered: %s (type=%s, priority=%s)",
                     rule.name, rule.rule_type, rule.priority.name)

    def deregister_rule(self, name: str) -> bool:
        """Remove a rule by name. Returns True if removed."""
        if name in self._rules:
            del self._rules[name]
            logger.info("Governance rule deregistered: %s", name)
            return True
        return False

    def enable_rule(self, name: str) -> bool:
        """Enable a disabled rule. Returns True if found and enabled."""
        rule = self._rules.get(name)
        if rule:
            rule.enabled = True
            logger.info("Governance rule enabled: %s", name)
            return True
        return False

    def disable_rule(self, name: str) -> bool:
        """Disable a rule without removing it. Returns True if found."""
        rule = self._rules.get(name)
        if rule:
            rule.enabled = False
            logger.info("Governance rule disabled: %s", name)
            return True
        return False

    def get_rule(self, name: str) -> Optional[GovernanceRule]:
        return self._rules.get(name)

    def list_rules(
        self,
        enabled_only: bool = False,
        rule_type: Optional[str] = None,
    ) -> List[GovernanceRule]:
        """List rules, optionally filtered."""
        rules = list(self._rules.values())
        if enabled_only:
            rules = [r for r in rules if r.enabled]
        if rule_type:
            rules = [r for r in rules if r.rule_type == rule_type]
        return sorted(rules, key=lambda r: (-int(r.priority), r.name))

    # ── Validation ──

    def validate(
        self,
        action: Any,
        context: Optional[Dict[str, Any]] = None,
        task_id: str = "",
    ) -> GovernanceResult:
        """Validate an action against all enabled governance rules.

        Args:
            action: The action to validate (dict, str, or any structured representation)
            context: Additional context (domain, risk_level, capability, user info, etc.)
            task_id: Optional task ID for audit linkage

        Returns:
            GovernanceResult with final status, matched/failed rules, and reason.
        """
        t0 = time.perf_counter()
        trace_id = task_id or f"{self._task_id_prefix}_{uuid.uuid4().hex[:12]}"
        ctx = context or {}

        # Sort rules by priority descending
        sorted_rules = sorted(
            [r for r in self._rules.values() if r.enabled and r.check_fn is not None],
            key=lambda r: -int(r.priority),
        )

        result = GovernanceResult(
            status=GovernanceStatus.PASSED,
            trace_id=trace_id,
        )

        for rule in sorted_rules:
            try:
                passed = rule.check_fn(action, ctx)
            except Exception as exc:
                logger.error("Governance rule '%s' raised exception: %s", rule.name, exc)
                passed = False  # fail-closed: exception = block

            if not passed:
                result.failed_rules.append(rule.name)

                if rule.priority >= RulePriority.CRITICAL:
                    # CRITICAL failure → immediate BLOCKED
                    result.status = GovernanceStatus.BLOCKED
                    result.reason = (
                        f"Blocked by CRITICAL rule '{rule.name}': {rule.description}"
                    )
                    result.matched_rules.append(rule.name)
                    break
                elif rule.priority >= RulePriority.HIGH:
                    # HIGH failure → NEEDS_APPROVAL (not immediate block)
                    result.status = GovernanceStatus.NEEDS_APPROVAL
                    result.matched_rules.append(rule.name)
                else:
                    # MEDIUM / LOW failure → flag for NEEDS_APPROVAL but can be overridden
                    if result.status == GovernanceStatus.PASSED:
                        result.status = GovernanceStatus.NEEDS_APPROVAL
                    result.matched_rules.append(rule.name)
            else:
                result.matched_rules.append(rule.name)

        # Post-validation: if NEEDS_APPROVAL and we have an approval engine, create request
        if result.needs_approval and self._approval_engine is not None:
            try:
                action_summary = self._summarize_action(action)
                domain = ctx.get("domain", "general")
                capability = ctx.get("capability", "")

                approval_req = self._approval_engine.create_request(
                    task_id=trace_id,
                    prompt=f"[Governance] {action_summary}",
                    domain=domain,
                    capability=capability,
                    extra={
                        "governance_trace_id": trace_id,
                        "matched_rules": result.matched_rules,
                        "failed_rules": result.failed_rules,
                        "reason": self._build_reason(result),
                    },
                )
                result.approval_request_id = approval_req.id
                result.reason = self._build_reason(result)
            except Exception as exc:
                logger.error("Failed to create approval request: %s", exc)
                result.status = GovernanceStatus.BLOCKED
                result.reason = f"Approval engine unavailable: {exc}"

        if not result.reason:
            result.reason = self._build_reason(result)

        result.duration_ms = (time.perf_counter() - t0) * 1000

        # Write audit trail
        self._audit_decision(result, action, ctx)

        logger.info(
            "Governance validation: trace=%s status=%s rules_matched=%d "
            "rules_failed=%d duration=%.1fms",
            trace_id, result.status, len(result.matched_rules),
            len(result.failed_rules), result.duration_ms,
        )
        return result

    # ── Helpers ──

    def _summarize_action(self, action: Any) -> str:
        """Create a short summary of the action for audit/approval display."""
        if isinstance(action, dict):
            tool = action.get("tool", action.get("action", ""))
            target = action.get("target", action.get("path", action.get("prompt", "")))
            return f"{tool}: {str(target)[:150]}"
        return str(action)[:200]

    def _build_reason(self, result: GovernanceResult) -> str:
        """Build a human-readable reason string."""
        if result.passed:
            return "All governance rules passed."
        if result.blocked:
            critical_rules = [r for r in result.failed_rules
                              if self._rules.get(r) and self._rules[r].priority >= RulePriority.CRITICAL]
            if critical_rules:
                return f"Blocked by critical rules: {', '.join(critical_rules)}"
            return f"Blocked by rules: {', '.join(result.failed_rules)}"
        # needs_approval
        return (
            f"Needs human approval: {len(result.failed_rules)} rule(s) flagged — "
            f"{', '.join(result.failed_rules[:5])}"
        )

    def _audit_decision(
        self,
        result: GovernanceResult,
        action: Any,
        context: Dict[str, Any],
    ) -> None:
        """Write immutable audit trail for this governance decision."""
        if self._audit_logger is None:
            return

        try:
            self._audit_logger.log(
                trace_id=result.trace_id,
                step=0,
                phase=DEFAULT_AUDIT_PHASE,
                actor="governance_agent",
                action="governance.validate",
                input_summary=self._summarize_action(action)[:200],
                output_summary=(
                    f"status={result.status} "
                    f"matched={result.matched_rules} "
                    f"failed={result.failed_rules}"
                )[:500],
                success=result.passed,
                error_msg="" if result.passed else result.reason[:500],
                duration_ms=result.duration_ms,
                extra={
                    "status": result.status,
                    "matched_rules": result.matched_rules,
                    "failed_rules": result.failed_rules,
                    "approval_request_id": result.approval_request_id,
                    "context_domain": context.get("domain", ""),
                },
            )
        except Exception as exc:
            logger.warning("Failed to write governance audit entry: %s", exc)

    # ── Built-in Rule Constructors ──

    @staticmethod
    def make_policy_rule(
        name: str,
        check_fn: Callable[[Any, Dict[str, Any]], bool],
        priority: RulePriority = RulePriority.HIGH,
        description: str = "",
    ) -> GovernanceRule:
        """Create a policy_compliance rule."""
        return GovernanceRule(
            name=name, rule_type="policy_compliance",
            priority=priority, check_fn=check_fn, description=description,
        )

    @staticmethod
    def make_rbac_rule(
        name: str,
        check_fn: Callable[[Any, Dict[str, Any]], bool],
        priority: RulePriority = RulePriority.CRITICAL,
        description: str = "",
    ) -> GovernanceRule:
        """Create an RBAC rule."""
        return GovernanceRule(
            name=name, rule_type="rbac",
            priority=priority, check_fn=check_fn, description=description,
        )

    @staticmethod
    def make_regulatory_rule(
        name: str,
        check_fn: Callable[[Any, Dict[str, Any]], bool],
        priority: RulePriority = RulePriority.CRITICAL,
        description: str = "",
    ) -> GovernanceRule:
        """Create a regulatory rule."""
        return GovernanceRule(
            name=name, rule_type="regulatory",
            priority=priority, check_fn=check_fn, description=description,
        )

    @staticmethod
    def make_business_rule(
        name: str,
        check_fn: Callable[[Any, Dict[str, Any]], bool],
        priority: RulePriority = RulePriority.MEDIUM,
        description: str = "",
    ) -> GovernanceRule:
        """Create a business_logic rule."""
        return GovernanceRule(
            name=name, rule_type="business_logic",
            priority=priority, check_fn=check_fn, description=description,
        )
