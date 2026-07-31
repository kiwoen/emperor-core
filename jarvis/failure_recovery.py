"""
Step-Level Failure Recovery Engine — 逐步故障恢复引擎

Solves the compound decay problem in multi-step workflows:
  85% per-step accuracy × 10 steps = only 19.7% end-to-end success rate.

Core components:
  - ErrorClassifier: classify failures into TRANSIENT / PERMANENT / DEGRADABLE
  - RetryPolicy: exponential backoff with jitter and configurable timeout
  - CircuitBreaker: CLOSED → OPEN → HALF_OPEN tri-state protection
  - FallbackStrategy: SKIP / DEFAULT / ALTERNATE_MODEL with priority chain
  - RecoveryEngine: orchestrates retry + circuit breaker + fallback

Usage:
    from jarvis.failure_recovery import RecoveryEngine, ErrorClassifier

    engine = RecoveryEngine()
    result = engine.execute_with_recovery(
        lambda: call_external_api(),
        context={"stage": "news_fetch"},
    )

Integration with ServicePipeline:
    Each Stage is automatically wrapped with a RecoveryEngine when
    ``use_recovery_engine=True`` is passed to ServicePipeline.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 1. Error Classification
# ═══════════════════════════════════════════════════════════════════


class ErrorCategory(Enum):
    """Failure category that determines recovery strategy."""

    TRANSIENT = "transient"       # Recoverable with retry (timeout, connection)
    PERMANENT = "permanent"       # Unrecoverable (validation, auth)
    DEGRADABLE = "degradable"     # Can continue with degraded functionality


# Classification mapping: (exception_type, substring_in_message) → ErrorCategory
_DEFAULT_CLASSIFICATION_RULES: List[Tuple[Type[BaseException], Optional[str], ErrorCategory]] = [
    # ── NETWORK / TIMEOUT: transient ──
    (TimeoutError, None, ErrorCategory.TRANSIENT),
    (ConnectionError, None, ErrorCategory.TRANSIENT),
    (ConnectionRefusedError, None, ErrorCategory.TRANSIENT),
    (ConnectionResetError, None, ErrorCategory.TRANSIENT),
    (ConnectionAbortedError, None, ErrorCategory.TRANSIENT),
    (BrokenPipeError, None, ErrorCategory.TRANSIENT),
    (asyncio.TimeoutError, None, ErrorCategory.TRANSIENT),
    # ── OSError subclasses with specific transient substrings ──
    (OSError, "timed out", ErrorCategory.TRANSIENT),
    (OSError, "connection", ErrorCategory.TRANSIENT),
    (OSError, "network", ErrorCategory.TRANSIENT),
    # ── PERMANENT: OSError subclasses (must be before generic OSError) ──
    (PermissionError, None, ErrorCategory.PERMANENT),
    (FileNotFoundError, None, ErrorCategory.PERMANENT),
    (FileExistsError, None, ErrorCategory.PERMANENT),
    # ── PERMANENT: validation / logic / auth ──
    (ValueError, None, ErrorCategory.PERMANENT),
    (TypeError, None, ErrorCategory.PERMANENT),
    (AttributeError, None, ErrorCategory.PERMANENT),
    (NotImplementedError, None, ErrorCategory.PERMANENT),
    (AssertionError, None, ErrorCategory.PERMANENT),
    (KeyError, None, ErrorCategory.PERMANENT),
    (IndexError, None, ErrorCategory.PERMANENT),
    (ImportError, None, ErrorCategory.PERMANENT),
    (ModuleNotFoundError, None, ErrorCategory.PERMANENT),
    (SyntaxError, None, ErrorCategory.PERMANENT),
    (NameError, None, ErrorCategory.PERMANENT),
    (RuntimeError, "permanent", ErrorCategory.PERMANENT),
    # ── DEGRADABLE: partial / quality / quota ──
    (RuntimeError, "degradable", ErrorCategory.DEGRADABLE),
    (RuntimeError, "degrade", ErrorCategory.DEGRADABLE),
    (RuntimeError, "fallback", ErrorCategory.DEGRADABLE),
    (Exception, "rate limit", ErrorCategory.DEGRADABLE),
    (Exception, "quota", ErrorCategory.DEGRADABLE),
    # ── CATCH-ALL for remaining OSError / IOError subclasses → TRANSIENT ──
    (OSError, None, ErrorCategory.TRANSIENT),
    # Default catch-all for unclassified RuntimeError → TRANSIENT (safe to retry)
    (RuntimeError, None, ErrorCategory.TRANSIENT),
]


class ErrorClassifier:
    """Classify exceptions into TRANSIENT / PERMANENT / DEGRADABLE categories.

    Uses a priority-ordered list of (type, substring, category) rules.
    First match wins.  Unmatched exceptions default to PERMANENT.
    """

    def __init__(self, rules: List[Tuple[Type[BaseException], Optional[str], ErrorCategory]] = None):
        self._rules: List[Tuple[Type[BaseException], Optional[str], ErrorCategory]] = (
            list(rules) if rules else list(_DEFAULT_CLASSIFICATION_RULES)
        )

    def classify(self, exc: BaseException) -> ErrorCategory:
        """Classify an exception into an ErrorCategory."""
        exc_type = type(exc)
        exc_msg = str(exc).lower()

        for rule_type, rule_msg, category in self._rules:
            # Check type match (issubclass to catch subclasses too)
            if not issubclass(exc_type, rule_type):
                continue
            # Check substring match (if specified)
            if rule_msg is not None and rule_msg not in exc_msg:
                continue
            return category

        # Ultimate fallback: treat unknown as PERMANENT
        return ErrorCategory.PERMANENT

    def add_rule(self, exc_type: Type[BaseException], message_substring: Optional[str],
                 category: ErrorCategory):
        """Add a classification rule (highest priority)."""
        self._rules.insert(0, (exc_type, message_substring, category))

    def is_retryable(self, exc: BaseException) -> bool:
        """Check whether an exception is worth retrying."""
        return self.classify(exc) == ErrorCategory.TRANSIENT


# ═══════════════════════════════════════════════════════════════════
# 2. Retry Policy
# ═══════════════════════════════════════════════════════════════════


@dataclass
class RetryPolicy:
    """Exponential backoff retry policy with jitter.

    Delay formula:
        delay = min(base_delay * (2 ** attempt) + jitter, max_delay)
    where jitter = random.uniform(0, base_delay * 0.5)

    Args:
        max_retries: Maximum retry attempts (total attempts = 1 + max_retries).
        base_delay: Initial backoff delay in seconds.
        max_delay: Upper cap on backoff delay in seconds.
        jitter: Whether to apply random jitter to avoid thundering herd.
        timeout: Optional per-attempt timeout in seconds.
    """

    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: bool = True
    timeout: Optional[float] = None

    def delay_for_attempt(self, attempt: int) -> float:
        """Calculate backoff delay for a given retry attempt (0-based)."""
        delay = min(self.base_delay * (2.0 ** attempt), self.max_delay)
        if self.jitter:
            delay += random.uniform(0, self.base_delay * 0.5)
        return delay


# ═══════════════════════════════════════════════════════════════════
# 3. Circuit Breaker
# ═══════════════════════════════════════════════════════════════════


class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation, calls pass through
    OPEN = "open"            # Tripped, calls fail fast
    HALF_OPEN = "half_open"  # Probing, allows one test call


class CircuitBreaker:
    """Tri-state circuit breaker with in-memory state persistence.

    States:
        CLOSED  ── failure_threshold consecutive failures ──→ OPEN
        OPEN    ── recovery_timeout elapsed ──→ HALF_OPEN
        HALF_OPEN ── success ──→ CLOSED
        HALF_OPEN ── failure ──→ OPEN
    """

    def __init__(self, name: str = "default",
                 failure_threshold: int = 5,
                 recovery_timeout: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0
        self._last_state_change: float = time.time()
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            # Auto-transition: OPEN → HALF_OPEN when timeout expires
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
            return self._state

    def allow_request(self) -> bool:
        """Check whether a request should be allowed through."""
        s = self.state
        if s == CircuitState.CLOSED:
            return True
        if s == CircuitState.HALF_OPEN:
            # Allow only one probe request at a time in HALF_OPEN.
            # Simple approach: always allow (the caller will call report_success/failure).
            return True
        # OPEN — fail fast
        return False

    def report_success(self):
        """Report a successful call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.CLOSED)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def report_failure(self):
        """Report a failed call, potentially tripping the breaker."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if (self._state == CircuitState.CLOSED
                    and self._failure_count >= self.failure_threshold):
                self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)

    def reset(self):
        """Force reset to CLOSED state."""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0

    def _transition_to(self, new_state: CircuitState):
        if self._state != new_state:
            self._state = new_state
            self._last_state_change = time.time()
            if new_state == CircuitState.CLOSED:
                self._failure_count = 0
            logger.debug("CircuitBreaker[%s] %s → %s", self.name,
                         self._state.name, new_state.name)

    def get_stats(self) -> Dict[str, Any]:
        """Return current breaker statistics."""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "last_failure_time": self._last_failure_time,
                "last_state_change": self._last_state_change,
                "recovery_timeout": self.recovery_timeout,
            }


# ═══════════════════════════════════════════════════════════════════
# 4. Fallback Strategy
# ═══════════════════════════════════════════════════════════════════


class FallbackType(Enum):
    SKIP = "skip"                  # Skip the step entirely
    DEFAULT = "default"            # Use a predefined default value
    ALTERNATE_MODEL = "alternate"  # Switch to a backup model / handler


@dataclass
class FallbackAction:
    """A single fallback action in a chain."""

    fallback_type: FallbackType
    default_value: Any = None           # Used when type == DEFAULT
    alternate_handler: Optional[Callable] = None  # Used when type == ALTERNATE_MODEL
    priority: int = 0                   # Lower = higher priority (executed first)


class FallbackChain:
    """Ordered chain of fallback actions, tried in priority order.

    Usage:
        chain = FallbackChain()
        chain.add(FallbackAction(FallbackType.SKIP, priority=10))
        chain.add(FallbackAction(FallbackType.DEFAULT, default_value={"result": "cached"}, priority=0))

        result = chain.execute(original_exception)
    """

    def __init__(self):
        self._actions: List[FallbackAction] = []

    def add(self, action: FallbackAction) -> FallbackChain:
        """Add a fallback action to the chain. Returns self for chaining."""
        self._actions.append(action)
        self._actions.sort(key=lambda a: a.priority)
        return self

    def execute(self, original_exception: BaseException = None) -> Tuple[FallbackType, Any]:
        """Execute fallback chain — returns (fallback_type, result_or_None).

        Returns:
            Tuple of (FallbackType, result). For SKIP, result is None.
            Raises RuntimeError if chain is empty.
        """
        if not self._actions:
            raise RuntimeError("FallbackChain is empty — cannot execute fallback")

        # Try actions in priority order
        for action in self._actions:
            if action.fallback_type == FallbackType.SKIP:
                return (FallbackType.SKIP, None)
            elif action.fallback_type == FallbackType.DEFAULT:
                return (FallbackType.DEFAULT, action.default_value)
            elif action.fallback_type == FallbackType.ALTERNATE_MODEL:
                if action.alternate_handler is not None:
                    try:
                        result = action.alternate_handler()
                        return (FallbackType.ALTERNATE_MODEL, result)
                    except Exception:
                        # Alternate handler also failed — try next in chain
                        continue
                # No handler or handler failed — continue to next action

        # All fallback actions exhausted
        raise RuntimeError(
            f"FallbackChain exhausted all {len(self._actions)} actions"
        )

    def is_empty(self) -> bool:
        return len(self._actions) == 0


# ═══════════════════════════════════════════════════════════════════
# 5. Recovery Engine
# ═══════════════════════════════════════════════════════════════════


class RecoveryResultStatus(Enum):
    SUCCESS = "success"                      # First attempt succeeded
    RETRY_SUCCESS = "retry_success"          # Succeeded after retry
    DEGRADED = "degraded"                    # Succeeded via fallback
    FAILED = "failed"                        # All strategies exhausted
    CIRCUIT_OPEN = "circuit_open"            # Blocked by circuit breaker


@dataclass
class RecoveryResult:
    """Result of a recovery engine execution."""

    status: RecoveryResultStatus
    output: Any = None
    error: Optional[str] = None
    attempts: int = 0
    fallback_type: Optional[FallbackType] = None
    total_time: float = 0


@dataclass
class RecoveryContext:
    """Context passed to the recovery engine for logging and decisions."""

    stage_name: str = ""
    pipeline_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class RecoveryEngine:
    """Orchestrates retry + circuit breaker + fallback for any callable.

    Usage:
        engine = RecoveryEngine()
        result = engine.execute_with_recovery(
            lambda: risky_api_call(),
            context=RecoveryContext(stage_name="news_fetch"),
        )
    """

    def __init__(self,
                 classifier: ErrorClassifier = None,
                 retry_policy: RetryPolicy = None,
                 circuit_breaker: CircuitBreaker = None,
                 fallback_chain: FallbackChain = None,
                 name: str = "default"):
        self.name = name
        self.classifier = classifier or ErrorClassifier()
        self.retry_policy = retry_policy or RetryPolicy()
        self.circuit_breaker = circuit_breaker or CircuitBreaker(name=name)
        self.fallback_chain = fallback_chain or FallbackChain()

        # Recovery statistics
        self._stats_lock = threading.Lock()
        self.success_count: int = 0
        self.retry_success_count: int = 0
        self.degraded_count: int = 0
        self.failed_count: int = 0
        self.circuit_open_count: int = 0

    def execute_with_recovery(
        self,
        func: Callable[[], Any],
        context: RecoveryContext = None,
    ) -> RecoveryResult:
        """Execute a callable with full recovery: retry → circuit breaker → fallback.

        Args:
            func: Zero-arg callable to execute.
            context: Optional stage/pipeline context for logging.

        Returns:
            RecoveryResult with status, output, and statistics.
        """
        start = time.time()
        ctx = context or RecoveryContext()

        # --- Stage 1: Circuit breaker check ---
        if not self.circuit_breaker.allow_request():
            self.circuit_open_count += 1
            logger.warning(
                "CircuitBreaker[%s] OPEN — failing fast for stage '%s'",
                self.name, ctx.stage_name,
            )
            # Try fallback directly
            if not self.fallback_chain.is_empty():
                return self._apply_fallback(None, start)
            return RecoveryResult(
                status=RecoveryResultStatus.CIRCUIT_OPEN,
                error=f"Circuit breaker [{self.name}] is OPEN",
                total_time=time.time() - start,
            )

        # --- Stage 2: Attempt execution with retry ---
        max_attempts = self.retry_policy.max_retries + 1
        last_exception: Optional[BaseException] = None

        for attempt in range(max_attempts):
            try:
                output = self._timed_call(func)
                # Success!
                self.circuit_breaker.report_success()
                with self._stats_lock:
                    if attempt == 0:
                        self.success_count += 1
                    else:
                        self.retry_success_count += 1

                status = (RecoveryResultStatus.SUCCESS if attempt == 0
                          else RecoveryResultStatus.RETRY_SUCCESS)
                logger.debug(
                    "Recovery[%s] stage='%s' success on attempt %d/%d",
                    self.name, ctx.stage_name, attempt + 1, max_attempts,
                )
                return RecoveryResult(
                    status=status,
                    output=output,
                    attempts=attempt + 1,
                    total_time=time.time() - start,
                )

            except Exception as exc:
                last_exception = exc
                category = self.classifier.classify(exc)

                logger.debug(
                    "Recovery[%s] stage='%s' attempt %d/%d failed: %s (%s)",
                    self.name, ctx.stage_name, attempt + 1, max_attempts,
                    str(exc)[:100], category.value,
                )

                # PERMANENT — no point retrying
                if category == ErrorCategory.PERMANENT:
                    break

                # TRANSIENT or DEGRADABLE — retry if attempts remain
                if attempt < max_attempts - 1:
                    delay = self.retry_policy.delay_for_attempt(attempt)
                    time.sleep(delay)
                # else: last attempt failed, will fall through

        # --- Stage 3: Report failure to circuit breaker ---
        self.circuit_breaker.report_failure()

        # --- Stage 4: Try fallback ---
        if not self.fallback_chain.is_empty():
            return self._apply_fallback(last_exception, start)

        # --- Stage 5: All strategies exhausted ---
        with self._stats_lock:
            self.failed_count += 1

        logger.warning(
            "Recovery[%s] stage='%s' ALL FAILED after %d attempts: %s",
            self.name, ctx.stage_name, max_attempts, str(last_exception)[:100],
        )
        return RecoveryResult(
            status=RecoveryResultStatus.FAILED,
            error=str(last_exception) if last_exception else "Unknown error",
            attempts=max_attempts,
            total_time=time.time() - start,
        )

    def _apply_fallback(self, exc: Optional[BaseException], start: float) -> RecoveryResult:
        """Execute fallback chain and wrap result."""
        exc_msg = str(exc) if exc else "circuit open"
        logger.info("Recovery[%s] applying fallback (reason: %s)", self.name, exc_msg[:80])

        try:
            fb_type, fb_result = self.fallback_chain.execute(exc)
            with self._stats_lock:
                self.degraded_count += 1

            return RecoveryResult(
                status=RecoveryResultStatus.DEGRADED,
                output=fb_result,
                error=str(exc) if exc else "",
                fallback_type=fb_type,
                total_time=time.time() - start,
            )
        except RuntimeError as fb_err:
            # Fallback chain exhausted
            with self._stats_lock:
                self.failed_count += 1
            return RecoveryResult(
                status=RecoveryResultStatus.FAILED,
                error=f"All retries+fallbacks exhausted. Original: {exc_msg}. Last: {fb_err}",
                total_time=time.time() - start,
            )

    def _timed_call(self, func: Callable[[], Any]) -> Any:
        """Execute func with optional timeout."""
        if self.retry_policy.timeout is not None:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(func)
                try:
                    return future.result(timeout=self.retry_policy.timeout)
                except concurrent.futures.TimeoutError:
                    future.cancel()
                    raise TimeoutError(
                        f"Callable timed out after {self.retry_policy.timeout}s"
                    )
        return func()

    def get_stats(self) -> Dict[str, Any]:
        """Return recovery statistics for this engine."""
        with self._stats_lock:
            return {
                "name": self.name,
                "success": self.success_count,
                "retry_success": self.retry_success_count,
                "degraded": self.degraded_count,
                "failed": self.failed_count,
                "circuit_open": self.circuit_open_count,
                "circuit_breaker": self.circuit_breaker.get_stats(),
            }

    def reset_stats(self):
        """Reset all counters."""
        with self._stats_lock:
            self.success_count = 0
            self.retry_success_count = 0
            self.degraded_count = 0
            self.failed_count = 0
            self.circuit_open_count = 0


# ═══════════════════════════════════════════════════════════════════
# Convenience: pre-built engines with common configurations
# ═══════════════════════════════════════════════════════════════════


def create_default_engine(name: str = "default") -> RecoveryEngine:
    """Create a RecoveryEngine with sensible defaults.

    Default configuration:
      - 3 retries with exponential backoff (0.5s, 1s, 2s)
      - Circuit breaker: 5 failures → open, 30s recovery timeout
      - Fallback chain: SKIP (lowest priority) as safety net
    """
    chain = FallbackChain()
    chain.add(FallbackAction(FallbackType.SKIP, priority=100))

    return RecoveryEngine(
        classifier=ErrorClassifier(),
        retry_policy=RetryPolicy(max_retries=3, base_delay=0.5, max_delay=30.0, jitter=True),
        circuit_breaker=CircuitBreaker(name=name, failure_threshold=5, recovery_timeout=30.0),
        fallback_chain=chain,
        name=name,
    )


def create_strict_engine(name: str = "strict") -> RecoveryEngine:
    """Create a strict engine: no fallback, fails hard on permanent errors."""
    return RecoveryEngine(
        classifier=ErrorClassifier(),
        retry_policy=RetryPolicy(max_retries=2, base_delay=1.0, max_delay=10.0, jitter=True),
        circuit_breaker=CircuitBreaker(name=name, failure_threshold=3, recovery_timeout=60.0),
        fallback_chain=FallbackChain(),  # empty — will fail
        name=name,
    )
