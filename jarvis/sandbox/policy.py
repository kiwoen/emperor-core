"""
Security Policy — code safety validation with permission levels.

Three built-in policy levels:

- ``READ_ONLY``:   No filesystem writes, no network, no subprocess spawning.
- ``RESTRICTED``:  Limited network (allowlist), limited filesystem (allowlist paths), no dangerous builtins.
- ``FULL_ACCESS``: Unrestricted (validation still runs but passes everything).

Usage::

    from jarvis.sandbox.policy import SecurityPolicy, PolicyLevel

    policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
    policy.allow_network("*.example.com")
    policy.allow_path("read", "/tmp/")

    result = policy.validate("import os; os.system('rm -rf /')", language="python")
    if not result.passed:
        print(result.violations)  # ["blocked: os.system"]
"""

from __future__ import annotations

import logging
import re
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.sandbox.policy")


# ═══════════════════════════════════════════════════════════════════
# Enums & Data
# ═══════════════════════════════════════════════════════════════════


class PolicyLevel(Enum):
    """Permission levels for sandbox execution."""

    READ_ONLY = "read_only"
    RESTRICTED = "restricted"
    FULL_ACCESS = "full_access"


class ValidationResult:
    """Outcome of a :meth:`SecurityPolicy.validate` call.

    Attributes:
        passed:      Whether the code passed all checks.
        level:       The policy level used.
        violations:  Human-readable descriptions of each violation.
    """

    def __init__(self, passed: bool, level: PolicyLevel, violations: list[str]) -> None:
        self.passed: bool = passed
        self.level: PolicyLevel = level
        self.violations: list[str] = violations

    def __repr__(self) -> str:
        return f"ValidationResult(passed={self.passed}, level={self.level.name}, violations={len(self.violations)})"


# ═══════════════════════════════════════════════════════════════════
# SecurityPolicy
# ═══════════════════════════════════════════════════════════════════


class SecurityPolicy:
    """Validates code safety before sandbox execution.

    Performs static analysis against configurable allowlists / blocklists.

    Parameters:
        level:           Default policy level.
        allowed_domains: Glob patterns for allowed network hosts.
        allowed_paths_read:   Absolute-path allowlist for reads (glob).
        allowed_paths_write:  Absolute-path allowlist for writes (glob).
    """

    # ── Dangerous patterns ──────────────────────────────────────────

    # Python: blacklist builtin / module / method patterns
    _PY_DANGEROUS_IMPORTS: tuple[str, ...] = (
        "os.system", "os.popen",
        "subprocess", "importlib.__import__",
        "socket",
        "shutil.rmtree", "shutil.move",
        "ctypes", "code",
    )

    _PY_DANGEROUS_BUILTINS: tuple[str, ...] = (
        "__import__", "eval", "exec", "compile", "globals", "locals",
        "open", "input",
    )

    _PY_DANGEROUS_PATTERNS: tuple[str, ...] = (
        r"os\.system\s*\(",
        r"os\.popen\s*\(",
        r"subprocess\.",
        r"shutil\.rmtree\s*\(",
        r"socket\.socket\s*\(",
        r"ctypes\.",
        r"__import__\s*\(",
    )

    # Shell: blocked keywords / commands
    _SHELL_DANGEROUS_COMMANDS: tuple[str, ...] = (
        "rm -rf", "del /F /S /Q", "rmdir /S /Q",
        "format", "mkfs",
        "dd if=", "shred",
        "> /dev/sda", "> /dev/sdb",
        ":(){ :|:& };:",  # fork bomb
    )

    # JavaScript: dangerous globals / patterns
    _JS_DANGEROUS_PATTERNS: tuple[str, ...] = (
        r"require\s*\(\s*['\"]child_process",
        r"require\s*\(\s*['\"]fs\b",
        r"require\s*\(\s*['\"]net\b",
        r"process\.exit\s*\(",
        r"eval\s*\(",
    )

    def __init__(
        self,
        level: PolicyLevel = PolicyLevel.RESTRICTED,
        allowed_domains: Optional[list[str]] = None,
        allowed_paths_read: Optional[list[str]] = None,
        allowed_paths_write: Optional[list[str]] = None,
    ) -> None:
        self.level: PolicyLevel = level

        # Network allowlist (glob patterns, e.g. "*.example.com", "192.168.*")
        self._allowed_domains: list[str] = list(allowed_domains or [])
        self._network_blocked: bool = level != PolicyLevel.FULL_ACCESS

        # Filesystem allowlists (absolute paths, glob-style supported)
        self._allowed_read: list[Path] = [Path(p) for p in (allowed_paths_read or [])]
        self._allowed_write: list[Path] = [Path(p) for p in (allowed_paths_write or [])]
        self._fs_read_blocked: bool = level == PolicyLevel.READ_ONLY
        self._fs_write_blocked: bool = level in (PolicyLevel.READ_ONLY, PolicyLevel.RESTRICTED)

        # Compiled regex cache
        self._py_dangerous_re: list[re.Pattern] = [
            re.compile(p) for p in self._PY_DANGEROUS_PATTERNS
        ]
        self._js_dangerous_re: list[re.Pattern] = [
            re.compile(p) for p in self._JS_DANGEROUS_PATTERNS
        ]

    # ── Network control ─────────────────────────────────────────────

    def allow_network(self, domain_glob: str) -> None:
        """Add a domain glob to the allowlist.

        When network is blocked (RESTRICTED / READ_ONLY), only allowlisted
        domains pass ``is_network_allowed``.  Does **not** lift the network
        block — use :meth:`set_level` with ``FULL_ACCESS`` for that.

        Example::

            policy.allow_network("*.google.com")
            policy.allow_network("192.168.1.*")
        """
        if domain_glob not in self._allowed_domains:
            self._allowed_domains.append(domain_glob)

    def block_network(self) -> None:
        """Disable all network access, clearing the allowlist."""
        self._network_blocked = True
        self._allowed_domains.clear()

    def is_network_allowed(self, target: str) -> bool:
        """Check whether *target* (domain or IP) is allowed.

        Returns ``True`` if network is unrestricted, otherwise checks the allowlist.
        """
        import fnmatch
        if not self._network_blocked:
            return True
        return any(fnmatch.fnmatch(target, pattern) for pattern in self._allowed_domains)

    # ── Filesystem control ──────────────────────────────────────────

    def allow_path(self, mode: str, path: str) -> None:
        """Add a filesystem path to the allowlist.

        When filesystem access is blocked by the policy level (reads in
        READ_ONLY; writes in READ_ONLY / RESTRICTED), only allowlisted
        paths pass ``is_path_allowed``.  Does **not** lift the filesystem
        block — use :meth:`set_level` for that.

        Args:
            mode: ``"read"`` or ``"write"``.
            path: Absolute path (glob patterns accepted).
        """
        p = Path(path)
        if mode == "read":
            if p not in self._allowed_read:
                self._allowed_read.append(p)
        elif mode == "write":
            if p not in self._allowed_write:
                self._allowed_write.append(p)
        else:
            raise ValueError(f"Invalid mode '{mode}'. Use 'read' or 'write'.")

    def is_path_allowed(self, mode: str, path: str) -> bool:
        """Check whether *path* access is permitted.

        Returns ``True`` if access is unrestricted, otherwise checks the allowlist.
        """
        import fnmatch
        p = Path(path).resolve()

        if mode == "read":
            if not self._fs_read_blocked:
                return True
            return self._match_path(p, self._allowed_read)
        elif mode == "write":
            if not self._fs_write_blocked:
                return True
            return self._match_path(p, self._allowed_write)
        return False

    @staticmethod
    def _match_path(target: Path, allowlist: list[Path]) -> bool:
        import fnmatch
        target_str = str(target).replace("\\", "/")
        for allowed in allowlist:
            allowed_str = str(allowed).replace("\\", "/")
            if fnmatch.fnmatch(target_str, allowed_str):
                return True
        return False

    # ── Validation ──────────────────────────────────────────────────

    def validate(
        self,
        code: str,
        language: str = "python",
        level: Optional[PolicyLevel] = None,
    ) -> ValidationResult:
        """Static-analyse *code* for safety violations.

        Args:
            code:     Source code to check.
            language: ``"python"``, ``"shell"``, or ``"javascript"``.
            level:    Override policy level for this check.

        Returns:
            :class:`ValidationResult` — ``passed=True`` only if zero violations.
        """
        level = level or self.level
        violations: list[str] = []

        if language == "python":
            violations = self._validate_python(code)
        elif language == "shell":
            violations = self._validate_shell(code)
        elif language == "javascript":
            violations = self._validate_javascript(code)
        else:
            return ValidationResult(False, level, [f"Unsupported language: {language}"])

        # Apply level-based filtering
        if level == PolicyLevel.FULL_ACCESS:
            return ValidationResult(True, level, [])

        passed = len(violations) == 0
        return ValidationResult(passed, level, violations)

    # ── Python validator ────────────────────────────────────────────

    def _validate_python(self, code: str) -> list[str]:
        violations: list[str] = []

        # Strip comments for cleaner matching
        clean = self._strip_python_comments(code)

        # Check dangerous patterns via regex
        for pat in self._py_dangerous_re:
            if pat.search(clean):
                violations.append(f"blocked pattern: {pat.pattern}")

        # Check dangerous imports (word-boundary matching)
        for imp in self._PY_DANGEROUS_IMPORTS:
            # e.g. "os.system" should match "import os; os.system(...)" but not "os_system"
            pattern = r"\b" + re.escape(imp) + r"\b"
            if re.search(pattern, clean):
                violations.append(f"blocked import/usage: {imp}")

        # Check dangerous builtins (READ_ONLY + RESTRICTED)
        if self.level in (PolicyLevel.READ_ONLY, PolicyLevel.RESTRICTED):
            for builtin in self._PY_DANGEROUS_BUILTINS:
                pattern = r"\b" + re.escape(builtin) + r"\s*\("
                if re.search(pattern, clean):
                    violations.append(f"blocked builtin: {builtin}")

        return violations

    # ── Shell validator ─────────────────────────────────────────────

    def _validate_shell(self, code: str) -> list[str]:
        violations: list[str] = []

        lower = code.lower()
        for cmd in self._SHELL_DANGEROUS_COMMANDS:
            if cmd.lower() in lower:
                violations.append(f"blocked command: {cmd}")

        return violations

    # ── JavaScript validator ────────────────────────────────────────

    def _validate_javascript(self, code: str) -> list[str]:
        violations: list[str] = []

        for pat in self._js_dangerous_re:
            if pat.search(code):
                violations.append(f"blocked pattern: {pat.pattern}")

        return violations

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _strip_python_comments(code: str) -> str:
        """Naively strip Python comments (``#`` and multi-line strings)."""
        # Remove single-line comments
        lines = []
        for line in code.splitlines():
            # Keep quoted # in strings (simple heuristic: only strip if # is outside quotes)
            stripped = re.sub(r'(?<![^\'\"])\#.*$', '', line)
            lines.append(stripped)
        result = "\n".join(lines)

        # Collapse triple-quoted strings to avoid false negatives from embedded code in strings
        result = re.sub(r'(\'\'\'.*?\'\'\')', '""', result, flags=re.DOTALL)
        result = re.sub(r'(""".*?""")', '""', result, flags=re.DOTALL)

        return result

    def set_level(self, level: PolicyLevel) -> None:
        """Switch the default policy level."""
        self.level = level
        # Update block flags
        self._network_blocked = level == PolicyLevel.READ_ONLY
        self._fs_read_blocked = level == PolicyLevel.READ_ONLY
        self._fs_write_blocked = level in (PolicyLevel.READ_ONLY, PolicyLevel.RESTRICTED)
