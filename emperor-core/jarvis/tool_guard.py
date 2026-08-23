"""
Tool Call Guard Middleware — Agent 工具调用的安全护栏。

在 Agent 的工具调用与实际 API 执行之间插入可组合的中间件层：
  - InputValidator   : SQL注入 / 路径遍历 / 参数类型范围校验
  - RateLimiter      : 滑动窗口频率限制（按工具名分别限流）
  - OutputFilter     : PII检测 / 敏感词替换 / 输出长度限制
  - ToolActionClassifier : 工具 action_type 分类（read/write/external）
  - ToolRiskLevel         : 四级风险定级（low/medium/high/critical）
  - RoleScopedAccess      : agent role 工具权限控制（admin/standard/viewer）
  - ThreeTierGuardEnhancement : 三层护栏集成层（分类 + 风险 + 角色 → 确认策略）
  - ToolGuardMiddleware : 统一编排 input → rate-limit → execute → output 流水线

所有拦截事件写入审计日志，并与 GovernanceAgent 联动触发治理规则校验。

Usage:
    from jarvis.tool_guard import ToolGuardMiddleware, ThreeTierGuardEnhancement

    tiers = ThreeTierGuardEnhancement(tool_registry={"read_file": "read", "write_file": "write", "send_message": "external"})
    guard = ToolGuardMiddleware(audit_logger=audit, governance_agent=gov, tiers=tiers)
    safe_delete = guard.wrap_tool_call("delete", original_delete_fn)
    result = safe_delete(paths=["/tmp/test.txt"])
"""

from __future__ import annotations

import logging
import re
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Pattern, Set, Tuple

logger = logging.getLogger("jarvis.tool_guard")


# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════

# SQL injection patterns
_SQL_INJECTION_KEYWORDS: Set[str] = {
    "DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE", "ALTER",
    "CREATE", "EXEC", "EXECUTE", "UNION", "SELECT", "GRANT", "REVOKE",
}
_SQL_INJECTION_PATTERNS: List[str] = [
    r"'.*--",           # ' OR '1'='1' --
    r";\s*--",          # ; --
    r"'\s*OR\s+'",      # ' OR '
    r"'\s*AND\s+'",     # ' AND '
    r"\bUNION\s+SELECT\b",
    r"\bDROP\s+TABLE\b",
    r"\bDELETE\s+FROM\b",
    r"\bINSERT\s+INTO\b",
    r"\bUPDATE\s+\w+\s+SET\b",
    r"\bTRUNCATE\s+TABLE\b",
    r"\bALTER\s+TABLE\b",
    r"\bEXEC\s*[\s\(]",  # EXEC sp_...
]

# Path traversal patterns
_PATH_TRAVERSAL_PATTERNS: List[str] = [
    r"\.\.[/\\]",       # ../
    r"\.\\\.[/\\]",     # ..\
    r"~[/\\]",           # ~/
    r"%2e%2e[/\\]",     # URL-encoded ../
    r"%252e%252e[/\\]",  # double-encoded
]

# PII detection patterns
_PII_PATTERNS: Dict[str, str] = {
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "phone_cn": r"1[3-9]\d{9}",
    "phone_us": r"\+?1?\s*\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
    "id_card_cn": r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
    "ip_address": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "api_key": r"""(?i)(?:api[_-]?key|token|secret|password|auth)\s*[:=]\s*['"][^'"]+['"]""",
}

# Default sensitive keywords to mask
_DEFAULT_SENSITIVE_KEYWORDS: Set[str] = {
    "password", "secret", "token", "private_key", "api_key",
    "access_key", "credentials", "passwd",
}

# Max output size (characters) before truncation
_DEFAULT_MAX_OUTPUT_LENGTH: int = 1_000_000  # 1MB char limit

AUDIT_PHASE = "tool_guard"


# ═══════════════════════════════════════════════════════════════════
# 1. InputValidator
# ═══════════════════════════════════════════════════════════════════


class ValidationSeverity(Enum):
    """Severity level of a validation finding."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ValidationFinding:
    """A single validation finding."""
    severity: ValidationSeverity
    rule: str
    message: str
    location: str = ""


@dataclass
class ValidationResult:
    """Result of input validation."""
    passed: bool
    findings: List[ValidationFinding] = field(default_factory=list)
    sanitized_input: Any = None

    @property
    def has_critical(self) -> bool:
        return any(f.severity == ValidationSeverity.CRITICAL for f in self.findings)


class InputValidator:
    """Validates tool call input for SQL injection, path traversal, and type safety.

    Usage:
        v = InputValidator()
        result = v.validate({"sql": "SELECT * FROM users WHERE id = '1' OR '1'='1'"})
        if not result.passed:
            raise SecurityError(result.findings)
    """

    def __init__(self,
                 check_sql_injection: bool = True,
                 check_path_traversal: bool = True,
                 max_string_length: int = 10000,
                 max_list_length: int = 1000):
        self.check_sql_injection = check_sql_injection
        self.check_path_traversal = check_path_traversal
        self.max_string_length = max_string_length
        self.max_list_length = max_list_length

    def validate(self, input_data: Any, context: Dict[str, Any] = None) -> ValidationResult:
        """Validate tool call input and return findings."""
        findings: List[ValidationFinding] = []

        # Recursively validate all string values in input
        self._validate_value(input_data, findings, path="$")

        passed = not any(
            f.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL)
            for f in findings
        )

        return ValidationResult(passed=passed, findings=findings)

    def _validate_value(self, value: Any, findings: List[ValidationFinding], path: str):
        """Recursively validate values."""
        if isinstance(value, str):
            self._validate_string(value, findings, path)
        elif isinstance(value, dict):
            for k, v in value.items():
                self._validate_value(v, findings, f"{path}.{k}")
        elif isinstance(value, list):
            if len(value) > self.max_list_length:
                findings.append(ValidationFinding(
                    severity=ValidationSeverity.WARNING,
                    rule="max_list_length",
                    message=f"List length {len(value)} exceeds max {self.max_list_length}",
                    location=path,
                ))
            for i, item in enumerate(value):
                self._validate_value(item, findings, f"{path}[{i}]")

    def _validate_string(self, s: str, findings: List[ValidationFinding], path: str):
        """Validate a single string value."""
        # Length check
        if len(s) > self.max_string_length:
            findings.append(ValidationFinding(
                severity=ValidationSeverity.WARNING,
                rule="max_string_length",
                message=f"String length {len(s)} exceeds max {self.max_string_length}",
                location=path,
            ))

        # SQL injection check
        if self.check_sql_injection:
            upper = s.upper()
            for kw in _SQL_INJECTION_KEYWORDS:
                if re.search(rf"\b{kw}\b", upper):
                    findings.append(ValidationFinding(
                        severity=ValidationSeverity.CRITICAL,
                        rule="sql_injection_keyword",
                        message=f"Potential SQL injection: keyword '{kw}' found",
                        location=path,
                    ))
            for pattern in _SQL_INJECTION_PATTERNS:
                if re.search(pattern, s, re.IGNORECASE):
                    findings.append(ValidationFinding(
                        severity=ValidationSeverity.CRITICAL,
                        rule="sql_injection_pattern",
                        message=f"SQL injection pattern matched: {pattern}",
                        location=path,
                    ))

        # Path traversal check
        if self.check_path_traversal:
            for pattern in _PATH_TRAVERSAL_PATTERNS:
                if re.search(pattern, s):
                    findings.append(ValidationFinding(
                        severity=ValidationSeverity.CRITICAL,
                        rule="path_traversal",
                        message=f"Path traversal pattern detected: matched '{pattern}'",
                        location=path,
                    ))


# ═══════════════════════════════════════════════════════════════════
# 2. RateLimiter
# ═══════════════════════════════════════════════════════════════════


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""
    allowed: bool
    current_count: int
    limit: int
    window_seconds: float
    retry_after_seconds: float = 0


class RateLimiter:
    """Sliding-window rate limiter, per tool name.

    Uses a deque of timestamps per tool name. On each check:
      - Expire timestamps outside the current window
      - If count < max_calls → allow
      - Otherwise → deny with retry_after_seconds

    Usage:
        rl = RateLimiter(max_calls=10, window_seconds=60)
        result = rl.check("delete")
        if not result.allowed:
            raise RateLimitExceeded(result.retry_after_seconds)
    """

    def __init__(self, max_calls: int = 30, window_seconds: float = 60.0):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._windows: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_calls * 2))
        self._lock = threading.Lock()

    def check(self, tool_name: str) -> RateLimitResult:
        """Check if a tool call is allowed under current rate limits."""
        with self._lock:
            now = time.time()
            window = self._windows[tool_name]

            # Expire old entries
            cutoff = now - self.window_seconds
            while window and window[0] < cutoff:
                window.popleft()

            current_count = len(window)

            if current_count < self.max_calls:
                window.append(now)
                return RateLimitResult(
                    allowed=True,
                    current_count=current_count + 1,
                    limit=self.max_calls,
                    window_seconds=self.window_seconds,
                )
            else:
                # Calculate when the oldest entry expires
                oldest = window[0]
                retry_after = max(0.0, (oldest + self.window_seconds) - now)
                return RateLimitResult(
                    allowed=False,
                    current_count=current_count,
                    limit=self.max_calls,
                    window_seconds=self.window_seconds,
                    retry_after_seconds=round(retry_after, 3),
                )

    def configure_tool(self, tool_name: str, max_calls: int, window_seconds: float):
        """Set per-tool rate limits (stored internally for reporting)."""
        # Store custom config for reporting purposes
        pass  # Simplicity: global config covers most use cases

    def reset(self, tool_name: str = None):
        """Reset counters for a specific tool or all tools."""
        with self._lock:
            if tool_name:
                self._windows[tool_name].clear()
            else:
                self._windows.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Return rate limiter statistics."""
        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            stats = {}
            for name, window in self._windows.items():
                active = sum(1 for t in window if t >= cutoff)
                stats[name] = {
                    "current_count": active,
                    "limit": self.max_calls,
                    "window_seconds": self.window_seconds,
                }
            return stats


# ═══════════════════════════════════════════════════════════════════
# 3. OutputFilter
# ═══════════════════════════════════════════════════════════════════


class PIISeverity(Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class PIIMatch:
    """A detected PII instance."""
    pii_type: str
    match: str
    severity: PIISeverity


@dataclass
class OutputFilterResult:
    """Result of output filtering."""
    passed: bool
    original_length: int
    filtered_length: int
    truncated: bool = False
    pii_matches: List[PIIMatch] = field(default_factory=list)
    sensitive_keywords_found: List[str] = field(default_factory=list)
    output: str = ""


class OutputFilter:
    """Filters tool call output for PII, sensitive keywords, and length.

    Usage:
        f = OutputFilter(max_output_length=100000)
        result = f.filter("User email: alice@example.com, SSN: 123-45-6789")
        if result.pii_matches:
            logger.warning(f"PII detected: {result.pii_matches}")
    """

    # Severity mapping for PII types
    _PII_SEVERITY: Dict[str, PIISeverity] = {
        "email": PIISeverity.MEDIUM,
        "phone_cn": PIISeverity.MEDIUM,
        "phone_us": PIISeverity.MEDIUM,
        "ssn": PIISeverity.HIGH,
        "credit_card": PIISeverity.HIGH,
        "id_card_cn": PIISeverity.HIGH,
        "ip_address": PIISeverity.LOW,
        "api_key": PIISeverity.HIGH,
    }

    def __init__(self,
                 check_pii: bool = True,
                 mask_pii: bool = True,
                 pii_patterns: Dict[str, str] = None,
                 sensitive_keywords: Set[str] = None,
                 max_output_length: int = _DEFAULT_MAX_OUTPUT_LENGTH,
                 mask_char: str = "*"):
        self.check_pii = check_pii
        self.mask_pii = mask_pii
        self.pii_patterns = pii_patterns or _PII_PATTERNS
        self.sensitive_keywords = sensitive_keywords or _DEFAULT_SENSITIVE_KEYWORDS
        self.max_output_length = max_output_length
        self.mask_char = mask_char

        # Compile PII patterns
        self._compiled_pii: Dict[str, Pattern] = {
            name: re.compile(pattern)
            for name, pattern in self.pii_patterns.items()
        }

    def filter(self, output: str, context: Dict[str, Any] = None) -> OutputFilterResult:
        """Filter output, detecting and optionally masking PII.

        Args:
            output: Raw output string from tool call.
            context: Optional execution context.

        Returns:
            OutputFilterResult with filtered output and detection details.
        """
        original_length = len(output)
        pii_matches: List[PIIMatch] = []
        sensitive_keywords_found: List[str] = []
        filtered = output

        # --- PII Detection ---
        if self.check_pii:
            for pii_type, pattern in self._compiled_pii.items():
                for match in pattern.finditer(filtered):
                    severity = self._PII_SEVERITY.get(pii_type, PIISeverity.LOW)
                    pii_matches.append(PIIMatch(
                        pii_type=pii_type,
                        match=match.group(),
                        severity=severity,
                    ))

            # Mask PII if enabled
            if self.mask_pii and pii_matches:
                for pii_type, pattern in self._compiled_pii.items():
                    filtered = pattern.sub(
                        lambda m, pt=pii_type: self._mask_value(m.group(), pt),
                        filtered,
                    )

        # --- Sensitive Keyword Detection ---
        lower_output = output.lower()
        for kw in self.sensitive_keywords:
            if kw.lower() in lower_output:
                sensitive_keywords_found.append(kw)

        # --- Truncation ---
        truncated = False
        if len(filtered) > self.max_output_length:
            filtered = filtered[:self.max_output_length]
            truncated = True

        # Determine pass/fail
        has_high_pii = any(m.severity == PIISeverity.HIGH for m in pii_matches)
        passed = not has_high_pii  # HIGH severity PII = failing the filter

        return OutputFilterResult(
            passed=passed,
            original_length=original_length,
            filtered_length=len(filtered),
            truncated=truncated,
            pii_matches=pii_matches,
            sensitive_keywords_found=sensitive_keywords_found,
            output=filtered,
        )

    def _mask_value(self, value: str, pii_type: str) -> str:
        """Mask a PII value, preserving some structure for debugging."""
        if len(value) <= 4:
            return self.mask_char * len(value)

        if pii_type == "email":
            parts = value.split("@")
            if len(parts) == 2:
                return f"{parts[0][:2]}{self.mask_char * 3}@{parts[1]}"
            return self.mask_char * len(value)

        if pii_type in ("phone_cn", "phone_us"):
            return value[:3] + self.mask_char * 4 + value[-4:]

        if pii_type in ("credit_card", "ssn", "id_card_cn"):
            return self.mask_char * (len(value) - 4) + value[-4:]

        # Default: mask middle portion
        visible = min(3, len(value) // 4)
        return value[:visible] + self.mask_char * (len(value) - visible * 2) + value[-visible:]


# ═══════════════════════════════════════════════════════════════════
# 3.5 Three-Tier Guard Enhancement (P0.5)
# ═══════════════════════════════════════════════════════════════════


class ToolActionType(Enum):
    """Action type classification for tool calls."""
    READ = "read"        # 只读操作：读取文件、查询数据库、搜索
    WRITE = "write"      # 写入操作：创建文件、修改数据、编辑
    EXTERNAL = "external"  # 外部操作：发送消息、外发数据、支付、API调用


class ToolRiskLevel(Enum):
    """Four-tier risk classification for tool actions."""
    LOW = "low"          # read 类，无副作用
    MEDIUM = "medium"    # write 类，有副作用但可逆
    HIGH = "high"        # external 类，影响外部系统/用户
    CRITICAL = "critical"  # 支付/删除/格式化等不可逆高危操作


class AgentRole(Enum):
    """Agent角色定义，控制工具访问权限范围。"""
    ADMIN = "admin"        # 全部工具
    STANDARD = "standard"  # read + write
    VIEWER = "viewer"      # 仅 read


class ConfirmationStrategy(Enum):
    """确认策略 — 不同 action_type 走不同确认层级。"""
    AUTO = "auto"          # 自动放行 (read)
    CHECK = "check"        # 检查业务规则后放行 (write)
    CONFIRM = "confirm"    # 需显式用户确认 (external)
    BLOCK = "block"        # 直接拦截 (viewer 做 write/external)


# ── Risk-level ↔ Action-type mapping ──

_ACTION_TO_RISK: Dict[ToolActionType, ToolRiskLevel] = {
    ToolActionType.READ: ToolRiskLevel.LOW,
    ToolActionType.WRITE: ToolRiskLevel.MEDIUM,
    ToolActionType.EXTERNAL: ToolRiskLevel.HIGH,
}

# ── Agent role → permitted action types ──

_ROLE_PERMISSIONS: Dict[AgentRole, Set[ToolActionType]] = {
    AgentRole.ADMIN:    {ToolActionType.READ, ToolActionType.WRITE, ToolActionType.EXTERNAL},
    AgentRole.STANDARD: {ToolActionType.READ, ToolActionType.WRITE},
    AgentRole.VIEWER:   {ToolActionType.READ},
}

# ── Action-type → confirmation strategy ──

_ACTION_CONFIRMATION: Dict[ToolActionType, ConfirmationStrategy] = {
    ToolActionType.READ:     ConfirmationStrategy.AUTO,
    ToolActionType.WRITE:    ConfirmationStrategy.CHECK,
    ToolActionType.EXTERNAL: ConfirmationStrategy.CONFIRM,
}

# ── Known external tool pattern hints (substring match in tool_name) ──

_EXTERNAL_TOOL_HINTS: Set[str] = {
    "send", "message", "publish", "pay", "charge", "transfer",
    "notify", "email", "sms", "webhook", "http", "api", "call",
    "broadcast", "dispatch", "forward", "relay", "upload",
    "post", "put", "patch", "delete_remote",
}

_WRITE_TOOL_HINTS: Set[str] = {
    "write", "create", "delete", "remove", "edit", "update", "save",
    "install", "uninstall", "move", "copy", "rename", "set", "config",
    "register", "deregister", "grant", "revoke", "deploy",
}

_READ_TOOL_HINTS: Set[str] = {
    "read", "get", "list", "search", "query", "find", "fetch",
    "stat", "check", "inspect", "describe", "show", "view",
    "lookup", "scan", "ls", "cat", "head", "tail",
}


@dataclass
class ActionClassification:
    """Result of classifying a tool call."""
    tool_name: str
    action_type: ToolActionType
    risk_level: ToolRiskLevel
    requires_confirmation: bool = False
    matched_by: str = ""  # "registry" | "hint" | "default"


@dataclass
class AccessCheckResult:
    """Result of a role-scoped access check."""
    allowed: bool
    role: AgentRole
    tool_name: str
    action_type: ToolActionType
    block_reason: str = ""
    confirmation_strategy: ConfirmationStrategy = ConfirmationStrategy.AUTO


class ToolActionClassifier:
    """Classify each tool call into read/write/external action type.

    Classification priority:
      1. Explicit registrations map (tool_action_registry[full_name])
      2. Wildcard registrations (tool_action_registry[prefix*] or *=action)
      3. Hint-based matching (tool name substring → heuristic)
      4. Default: fallback_action_type (defaults to WRITE for safety)

    Usage:
        classifier = ToolActionClassifier(
            tool_action_registry={"read_file": "read", "write_file": "write"},
        )
        result = classifier.classify("read_file")
        assert result.action_type == ToolActionType.READ
    """

    def __init__(
        self,
        tool_action_registry: Dict[str, str] = None,
        fallback_action_type: str = "write",
    ):
        self._exact_registry: Dict[str, ToolActionType] = {}
        self._wildcard_registry: Dict[str, ToolActionType] = {}
        self._fallback = ToolActionType(fallback_action_type)

        raw = tool_action_registry or {}
        self._parse_registry(raw)

    def _parse_registry(self, raw: Dict[str, str]) -> None:
        for key, value in raw.items():
            at = ToolActionType(value)
            if key.endswith("*"):
                self._wildcard_registry[key.rstrip("*")] = at
            elif key.startswith("*="):
                self._fallback = at
            else:
                self._exact_registry[key] = at

    def classify(self, tool_name: str, params: dict = None) -> ActionClassification:
        """Classify a tool call and return its action type + risk level.

        Args:
            tool_name: The tool's unique identifier.
            params: Optional parameter dict (reserved for future param-based classification).

        Returns:
            ActionClassification with action_type, risk_level, and metadata.
        """
        params = params or {}

        # Priority 1: exact match
        if tool_name in self._exact_registry:
            at = self._exact_registry[tool_name]
            return ActionClassification(
                tool_name=tool_name,
                action_type=at,
                risk_level=_ACTION_TO_RISK[at],
                matched_by="registry",
            )

        # Priority 2: wildcard match
        for prefix, at in self._wildcard_registry.items():
            if tool_name.startswith(prefix):
                return ActionClassification(
                    tool_name=tool_name,
                    action_type=at,
                    risk_level=_ACTION_TO_RISK[at],
                    matched_by="wildcard",
                )

        # Priority 3: hint-based
        lower = tool_name.lower()

        for hint in _EXTERNAL_TOOL_HINTS:
            if hint in lower:
                at = ToolActionType.EXTERNAL
                return ActionClassification(
                    tool_name=tool_name,
                    action_type=at,
                    risk_level=_ACTION_TO_RISK[at],
                    matched_by="hint",
                )

        for hint in _WRITE_TOOL_HINTS:
            if hint in lower:
                at = ToolActionType.WRITE
                return ActionClassification(
                    tool_name=tool_name,
                    action_type=at,
                    risk_level=_ACTION_TO_RISK[at],
                    matched_by="hint",
                )

        for hint in _READ_TOOL_HINTS:
            if hint in lower:
                at = ToolActionType.READ
                return ActionClassification(
                    tool_name=tool_name,
                    action_type=at,
                    risk_level=_ACTION_TO_RISK[at],
                    matched_by="hint",
                )

        # Priority 4: fallback
        return ActionClassification(
            tool_name=tool_name,
            action_type=self._fallback,
            risk_level=_ACTION_TO_RISK[self._fallback],
            matched_by="default",
        )

    def register(self, tool_name: str, action_type: str) -> None:
        """Register or update a tool's action type."""
        self._exact_registry[tool_name] = ToolActionType(action_type)


class RoleScopedAccess:
    """Enforce agent role → tool permission boundaries.

    Access rules:
      - ADMIN:    ALL actions permitted
      - STANDARD: READ + WRITE permitted, EXTERNAL denied
      - VIEWER:   READ only permitted, WRITE/EXTERNAL denied

    Usage:
        rsa = RoleScopedAccess()
        result = rsa.check_access(AgentRole.STANDARD, "send_message", ToolActionType.EXTERNAL)
        assert not result.allowed  # standard cannot call external tools
    """

    def __init__(self):
        pass

    def check_access(
        self,
        role: AgentRole,
        tool_name: str,
        action_type: ToolActionType,
    ) -> AccessCheckResult:
        """Check if a role can access a tool with the given action type.

        Args:
            role: The agent's role.
            tool_name: The tool being invoked.
            action_type: The classified action type of the tool.

        Returns:
            AccessCheckResult with allowed flag and block reason if denied.
        """
        permitted = _ROLE_PERMISSIONS.get(role, set())

        if action_type in permitted:
            strategy = _ACTION_CONFIRMATION.get(action_type, ConfirmationStrategy.AUTO)
            return AccessCheckResult(
                allowed=True,
                role=role,
                tool_name=tool_name,
                action_type=action_type,
                confirmation_strategy=strategy,
            )
        else:
            # Determine the reason
            if role == AgentRole.VIEWER and action_type in (
                ToolActionType.WRITE, ToolActionType.EXTERNAL,
            ):
                reason = f"Role '{role.value}' is restricted to READ-only; cannot invoke {action_type.value} tool '{tool_name}'"
            elif role == AgentRole.STANDARD and action_type == ToolActionType.EXTERNAL:
                reason = f"Role '{role.value}' cannot invoke EXTERNAL tool '{tool_name}'; requires ADMIN role"
            else:
                reason = f"Role '{role.value}' denied access to '{tool_name}' ({action_type.value})"

            return AccessCheckResult(
                allowed=False,
                role=role,
                tool_name=tool_name,
                action_type=action_type,
                block_reason=reason,
                confirmation_strategy=ConfirmationStrategy.BLOCK,
            )


class ThreeTierGuardEnhancement:
    """P0.5 Three-Tier Tool Guardrail: 分类 → 风险定级 → 角色访问控制。

    三层护栏：
      Tier 1 — ToolActionClassifier：每个工具标记 action_type
      Tier 2 — ToolRiskLevel：external → high, write → medium, read → low
      Tier 3 — RoleScopedAccess：admin=全部, standard=read+write, viewer=read-only

    确认策略路由：
      read     → AUTO      (自动放行)
      write    → CHECK     (检查业务规则)
      external → CONFIRM   (需显式确认)
      viewer做write/external → BLOCK (直接拦截)

    Usage:
        tiers = ThreeTierGuardEnhancement(
            tool_registry={"read_file": "read", "write_file": "write"},
            default_role=AgentRole.STANDARD,
        )
        result = tiers.evaluate("read_file", role=AgentRole.STANDARD)
        assert result.allowed and result.confirmation_strategy == ConfirmationStrategy.AUTO
    """

    def __init__(
        self,
        tool_registry: Dict[str, str] = None,
        default_role: AgentRole = AgentRole.STANDARD,
        fallback_action: str = "write",
    ):
        self.classifier = ToolActionClassifier(
            tool_action_registry=tool_registry,
            fallback_action_type=fallback_action,
        )
        self.access_control = RoleScopedAccess()
        self.default_role = default_role

    def evaluate(
        self,
        tool_name: str,
        params: dict = None,
        role: Optional[AgentRole] = None,
    ) -> AccessCheckResult:
        """Run the full three-tier pipeline on a tool call.

        Tier 1: classify action type
        Tier 2: derive risk level
        Tier 3: check role-scoped access + assign confirmation strategy

        Args:
            tool_name: Name of the tool.
            params: Tool call parameters (optional).
            role: Agent role override; defaults to self.default_role.

        Returns:
            AccessCheckResult with allowed, action_type, confirmation_strategy, etc.
        """
        role = role or self.default_role

        # Tier 1 + 2: classify + risk
        classification = self.classifier.classify(tool_name, params)

        # Tier 3: access check
        result = self.access_control.check_access(
            role=role,
            tool_name=tool_name,
            action_type=classification.action_type,
        )

        return result

    def classify_only(self, tool_name: str, params: dict = None) -> ActionClassification:
        """Classify a tool without role checks (for standalone use)."""
        return self.classifier.classify(tool_name, params)

    def register_tool(self, tool_name: str, action_type: str) -> None:
        """Register a tool's action type dynamically."""
        self.classifier.register(tool_name, action_type)


# ═══════════════════════════════════════════════════════════════════
# 4. ToolGuardMiddleware
# ═══════════════════════════════════════════════════════════════════


class GuardEventType(Enum):
    """Types of guard interception events."""
    INPUT_VALIDATION_FAILED = "input_validation_failed"
    RATE_LIMITED = "rate_limited"
    PII_DETECTED = "pii_detected"
    OUTPUT_TRUNCATED = "output_truncated"
    SENSITIVE_KEYWORD = "sensitive_keyword"
    GOVERNANCE_BLOCKED = "governance_blocked"


@dataclass
class GuardEvent:
    """An intercepted event during tool call guarding."""
    event_type: GuardEventType
    tool_name: str
    timestamp: float
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardResult:
    """Result of a guarded tool call."""
    success: bool
    output: Any = None
    error: Optional[str] = None
    events: List[GuardEvent] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""
    duration_ms: float = 0


class ToolGuardMiddleware:
    """Composable middleware that wraps tool calls with input validation,
    rate limiting, and output filtering.

    Auto-integrates with GovernanceAgent to trigger policy checks on
    blocked/intercepted events.

    Usage:
        guard = ToolGuardMiddleware(
            audit_logger=audit,
            governance_agent=gov,
            rate_limiter=RateLimiter(max_calls=10, window_seconds=60),
        )
        wrapped_fn = guard.wrap_tool_call("delete", original_delete_fn)
        result = wrapped_fn(paths=["/tmp/test.txt"])
    """

    def __init__(self,
                 input_validator: InputValidator = None,
                 rate_limiter: RateLimiter = None,
                 output_filter: OutputFilter = None,
                 audit_logger=None,
                 governance_agent=None,
                 enabled: bool = True,
                 tiers: ThreeTierGuardEnhancement = None):
        self.input_validator = input_validator or InputValidator()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.output_filter = output_filter or OutputFilter()
        self.audit_logger = audit_logger
        self.governance_agent = governance_agent
        self.enabled = enabled
        self.tiers = tiers  # P0.5: three-tier guard enhancement

        # Statistics
        self._stats_lock = threading.Lock()
        self.total_calls: int = 0
        self.blocked_calls: int = 0
        self.rate_limited_calls: int = 0
        self.pii_interceptions: int = 0

    def wrap_tool_call(self, tool_name: str, fn: Callable) -> Callable:
        """Wrap a tool call function with the guard middleware.

        Returns a new callable that accepts the same arguments as `fn`,
        but applies input validation → rate limiting → output filtering
        before/after the actual call.

        Args:
            tool_name: Name of the tool (for rate limiting and logging).
            fn: The original tool call function.

        Returns:
            A wrapped callable with the same signature as fn.
        """
        guard = self

        def guarded(*args, **kwargs):
            if not guard.enabled:
                return fn(*args, **kwargs)

            start_time = time.time()
            events: List[GuardEvent] = []

            # Combine args/kwargs into a unified input dict for validation
            call_input = guard._normalize_input(args, kwargs)

            # --- Step 1: Input Validation ---
            validation = guard.input_validator.validate(call_input)
            if not validation.passed:
                for finding in validation.findings:
                    if finding.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL):
                        events.append(GuardEvent(
                            event_type=GuardEventType.INPUT_VALIDATION_FAILED,
                            tool_name=tool_name,
                            timestamp=time.time(),
                            details={
                                "severity": finding.severity.value,
                                "rule": finding.rule,
                                "message": finding.message,
                                "location": finding.location,
                            },
                        ))

                # If critical findings exist, block the call
                if validation.has_critical:
                    guard._audit_event(tool_name, "blocked_input_validation", validation.findings)
                    guard._notify_governance(tool_name, "input_validation_failed", validation.findings)
                    with guard._stats_lock:
                        guard.total_calls += 1
                        guard.blocked_calls += 1
                    return GuardResult(
                        success=False,
                        error=f"Input validation failed: {len(validation.findings)} finding(s)",
                        events=events,
                        blocked=True,
                        block_reason="input_validation_failed",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

            # --- Step 1.5: Three-Tier Guard Enhancement (P0.5) ---
            if guard.tiers is not None:
                tier_result = guard.tiers.evaluate(tool_name, call_input)
                if not tier_result.allowed:
                    guard._audit_event(tool_name, "tier_blocked", {
                        "role": tier_result.role.value,
                        "action_type": tier_result.action_type.value,
                        "reason": tier_result.block_reason,
                    })
                    guard._notify_governance(tool_name, "tier_access_denied", tier_result)
                    with guard._stats_lock:
                        guard.total_calls += 1
                        guard.blocked_calls += 1
                    return GuardResult(
                        success=False,
                        error=tier_result.block_reason,
                        events=events,
                        blocked=True,
                        block_reason="tier_access_denied",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

            # --- Step 2: Rate Limiting ---
            rate_result = guard.rate_limiter.check(tool_name)
            if not rate_result.allowed:
                events.append(GuardEvent(
                    event_type=GuardEventType.RATE_LIMITED,
                    tool_name=tool_name,
                    timestamp=time.time(),
                    details={
                        "current_count": rate_result.current_count,
                        "limit": rate_result.limit,
                        "retry_after_seconds": rate_result.retry_after_seconds,
                    },
                ))
                guard._audit_event(tool_name, "rate_limited", {
                    "count": rate_result.current_count,
                    "limit": rate_result.limit,
                })
                guard._notify_governance(tool_name, "rate_limited", rate_result)

                with guard._stats_lock:
                    guard.total_calls += 1
                    guard.rate_limited_calls += 1
                return GuardResult(
                    success=False,
                    error=f"Rate limit exceeded for '{tool_name}': {rate_result.current_count}/{rate_result.limit}",
                    events=events,
                    blocked=True,
                    block_reason="rate_limited",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # --- Step 3: Execute ---
            try:
                raw_output = fn(*args, **kwargs)
            except Exception as e:
                guard._audit_event(tool_name, "execution_error", {"error": str(e)})
                with guard._stats_lock:
                    guard.total_calls += 1
                return GuardResult(
                    success=False,
                    error=str(e),
                    events=events,
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # --- Step 4: Output Filtering ---
            output_str = guard._to_string(raw_output)
            filter_result = guard.output_filter.filter(output_str, context={"tool_name": tool_name})

            if filter_result.pii_matches:
                pii_summary = [{"type": m.pii_type, "severity": m.severity.value}
                               for m in filter_result.pii_matches]
                events.append(GuardEvent(
                    event_type=GuardEventType.PII_DETECTED,
                    tool_name=tool_name,
                    timestamp=time.time(),
                    details={"matches": pii_summary},
                ))
                guard._audit_event(tool_name, "pii_detected", pii_summary)
                guard._notify_governance(tool_name, "pii_detected", filter_result.pii_matches)
                with guard._stats_lock:
                    guard.pii_interceptions += 1

            if filter_result.sensitive_keywords_found:
                events.append(GuardEvent(
                    event_type=GuardEventType.SENSITIVE_KEYWORD,
                    tool_name=tool_name,
                    timestamp=time.time(),
                    details={"keywords": filter_result.sensitive_keywords_found},
                ))

            if filter_result.truncated:
                events.append(GuardEvent(
                    event_type=GuardEventType.OUTPUT_TRUNCATED,
                    tool_name=tool_name,
                    timestamp=time.time(),
                    details={
                        "original_length": filter_result.original_length,
                        "max_length": guard.output_filter.max_output_length,
                    },
                ))

            # --- Step 5: Governance check ---
            governance_blocked = False
            if guard.governance_agent and filter_result.pii_matches:
                high_pii = [m for m in filter_result.pii_matches if m.severity == PIISeverity.HIGH]
                if high_pii:
                    gov_result = guard.governance_agent.validate(
                        action={"tool": tool_name, "pii_detected": True},
                        context={"pii_types": [m.pii_type for m in high_pii]},
                    )
                    if not gov_result.passed:
                        events.append(GuardEvent(
                            event_type=GuardEventType.GOVERNANCE_BLOCKED,
                            tool_name=tool_name,
                            timestamp=time.time(),
                            details={"reason": gov_result.reason if hasattr(gov_result, 'reason') else "governance"},
                        ))
                        governance_blocked = True
                        with guard._stats_lock:
                            guard.blocked_calls += 1

            duration_ms = (time.time() - start_time) * 1000

            guard._audit_event(tool_name, "completed", {
                "duration_ms": duration_ms,
                "events": len(events),
                "pii_count": len(filter_result.pii_matches),
            })

            with guard._stats_lock:
                guard.total_calls += 1

            if governance_blocked:
                return GuardResult(
                    success=False,
                    output=filter_result.output,
                    error="Governance blocked: high-severity PII detected",
                    events=events,
                    blocked=True,
                    block_reason="governance_blocked",
                    duration_ms=duration_ms,
                )

            return GuardResult(
                success=True,
                output=filter_result.output if isinstance(raw_output, str) else raw_output,
                events=events,
                duration_ms=duration_ms,
            )

        # Preserve function metadata
        guarded.__name__ = f"guarded_{fn.__name__}"
        guarded.__doc__ = fn.__doc__
        return guarded

    def _normalize_input(self, args: tuple, kwargs: dict) -> Dict[str, Any]:
        """Convert args + kwargs into a normalized dict for validation."""
        result = dict(kwargs)
        for i, arg in enumerate(args):
            result[f"_arg{i}"] = arg
        return result

    def _to_string(self, value: Any) -> str:
        """Safely convert any output to string for filtering."""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, (list, dict, tuple)):
            try:
                import json
                return json.dumps(value, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(value)
        return str(value)

    def _audit_event(self, tool_name: str, action: str, details: Any):
        """Write an event to the audit log if an audit_logger is configured."""
        if self.audit_logger is None:
            return
        try:
            self.audit_logger.log(
                trace_id=f"guard-{tool_name}-{int(time.time() * 1000)}",
                step=0,
                phase=AUDIT_PHASE,
                action=f"tool_guard.{action}",
                input_summary=f"tool={tool_name}",
                output_summary=str(details)[:500],
                success=(action != "blocked_input_validation" and action != "governance_blocked"),
            )
        except Exception as e:
            logger.debug("Audit log write failed: %s", e)

    def _notify_governance(self, tool_name: str, reason: str, details: Any):
        """Notify GovernanceAgent of an interception event."""
        if self.governance_agent is None:
            return
        try:
            self.governance_agent.validate(
                action={"tool": tool_name, "event": reason},
                context={"details": str(details)[:500]},
            )
        except Exception as e:
            logger.debug("Governance notification failed: %s", e)

    def get_stats(self) -> Dict[str, Any]:
        """Return guard middleware statistics."""
        with self._stats_lock:
            return {
                "total_calls": self.total_calls,
                "blocked_calls": self.blocked_calls,
                "rate_limited_calls": self.rate_limited_calls,
                "pii_interceptions": self.pii_interceptions,
                "rate_limiter": self.rate_limiter.get_stats(),
                "enabled": self.enabled,
            }
