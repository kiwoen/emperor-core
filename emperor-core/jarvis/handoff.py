"""
Multi-Agent Handoff Protocol — standardized context-passing between Ministers.

Inspired by OpenAI Agents SDK Handoffs design. Provides a structured,
auditable, and safe mechanism for Minister A to hand off a task to
Minister B with full context preservation.

Core abstractions:
    HandoffRequest      — what to hand off (target, task, context, metadata)
    HandoffContext       — serializable context blob carried across handoffs
    HandoffProtocol      — orchestration layer: validate, execute, fallback, trace

Key features:
    - Context serialization / deserialization (JSON-safe)
    - Priority inheritance (task priority propagates through handoff chain)
    - Fallback strategies (RETRY / REJECT / DELEGATE_TO_EMPEROR)
    - Timeout protection (handoff deadline enforcement)
    - Handoff chain tracking (full audit trail of minister → minister flow)

Usage:
    from jarvis.handoff import HandoffProtocol, HandoffRequest, HandoffContext

    protocol = HandoffProtocol()

    ctx = HandoffContext(
        task_id="task-001",
        original_prompt="Solve complex math problem",
        priority=3,
        history=[{"minister": "turing", "result": "partial..."}],
    )

    req = HandoffRequest(
        source_minister="turing",
        target_minister="gauss",
        context=ctx,
        reason="Specialized math required",
    )

    result = protocol.handoff(req)
    if result.accepted:
        # Minister B now owns the task
        pass
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("jarvis.handoff")


# ══════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════


class HandoffStatus(Enum):
    """Outcome of a handoff attempt."""
    ACCEPTED = "accepted"           # Target minister accepted the handoff
    REJECTED = "rejected"           # Target minister rejected the handoff
    TIMEOUT = "timeout"             # Handoff exceeded deadline
    FALLBACK = "fallback"           # Handoff failed, fallback strategy applied
    ERROR = "error"                 # Unexpected error during handoff


class FallbackStrategy(Enum):
    """What to do when a handoff fails."""
    RETRY = "retry"                         # Retry with same target
    RETRY_NEXT = "retry_next"               # Try next candidate minister
    REJECT = "reject"                       # Reject and return to source
    DELEGATE_TO_EMPEROR = "delegate_to_emperor"  # Escalate to Emperor


class HandoffPriority(IntEnum):
    """Task priority that propagates through handoff chain."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def from_int(cls, value: int) -> "HandoffPriority":
        if value >= 4:
            return cls.CRITICAL
        if value >= 3:
            return cls.HIGH
        if value >= 2:
            return cls.MEDIUM
        return cls.LOW

    @classmethod
    def from_string(cls, s: str) -> "HandoffPriority":
        mapping = {
            "critical": cls.CRITICAL,
            "high": cls.HIGH,
            "medium": cls.MEDIUM,
            "low": cls.LOW,
        }
        return mapping.get(s.lower(), cls.MEDIUM)


# ══════════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════════


@dataclass
class HandoffContext:
    """Serializable context blob carried across handoffs.

    This is the "payload" that travels from Minister A → Minister B,
    containing everything Minister B needs to continue the task.

    Attributes:
        task_id: Unique task identifier (maintained across handoffs)
        original_prompt: The original user prompt / task description
        priority: Task priority (propagated through chain)
        history: Ordered list of {minister, result, timestamp} entries
        data: Arbitrary JSON-safe key-value data accumulated during processing
        metadata: Extra metadata (tags, domain hints, etc.)
        created_at: Unix timestamp when this context was first created
        updated_at: Unix timestamp of last update
    """
    task_id: str = ""
    original_prompt: str = ""
    priority: int = 2  # HandoffPriority.MEDIUM
    history: List[Dict[str, Any]] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self):
        now = time.time()
        if self.created_at == 0.0:
            self.created_at = now
        if self.updated_at == 0.0:
            self.updated_at = now
        if not self.task_id:
            self.task_id = uuid.uuid4().hex[:12]

    @property
    def handoff_count(self) -> int:
        """How many handoffs have occurred in this chain."""
        return len(self.history)

    @property
    def chain(self) -> List[str]:
        """Ordered list of minister names in the handoff chain."""
        return [h.get("minister", "unknown") for h in self.history]

    def record_step(self, minister: str, result: Any, status: str = "completed") -> None:
        """Record a minister's contribution to the handoff history."""
        self.history.append({
            "minister": minister,
            "result": result,
            "status": status,
            "timestamp": time.time(),
        })
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dictionary."""
        return {
            "task_id": self.task_id,
            "original_prompt": self.original_prompt,
            "priority": self.priority,
            "history": self.history,
            "data": self.data,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "handoff_count": self.handoff_count,
            "chain": self.chain,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HandoffContext":
        """Deserialize from dictionary."""
        return cls(
            task_id=d.get("task_id", ""),
            original_prompt=d.get("original_prompt", ""),
            priority=d.get("priority", 2),
            history=d.get("history", []),
            data=d.get("data", {}),
            metadata=d.get("metadata", {}),
            created_at=d.get("created_at", 0.0),
            updated_at=d.get("updated_at", 0.0),
        )

    def serialize(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def deserialize(cls, s: str) -> "HandoffContext":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(s))


@dataclass
class HandoffRequest:
    """A handoff request from one minister to another.

    Attributes:
        handoff_id: Unique identifier for this handoff event
        source_minister: Name of the minister initiating the handoff
        target_minister: Name of the minister receiving the handoff
        context: The HandoffContext being passed
        reason: Human-readable reason for the handoff
        priority: Effective priority (inherited from context if not set)
        deadline_seconds: Maximum time to wait for handoff acceptance
        fallback_strategy: What to do if handoff fails
        candidate_ministers: Ordered list of fallback ministers
        metadata: Extra metadata
    """
    handoff_id: str = ""
    source_minister: str = ""
    target_minister: str = ""
    context: HandoffContext = field(default_factory=HandoffContext)
    reason: str = ""
    priority: int = 2
    deadline_seconds: float = 30.0
    fallback_strategy: FallbackStrategy = FallbackStrategy.REJECT
    candidate_ministers: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.handoff_id:
            self.handoff_id = f"ho_{uuid.uuid4().hex[:12]}"
        # Inherit priority from context if not explicitly set
        if self.priority == 2 and self.context.priority != 2:
            self.priority = self.context.priority

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "source_minister": self.source_minister,
            "target_minister": self.target_minister,
            "context": self.context.to_dict(),
            "reason": self.reason,
            "priority": self.priority,
            "deadline_seconds": self.deadline_seconds,
            "fallback_strategy": self.fallback_strategy.value,
            "candidate_ministers": self.candidate_ministers,
            "metadata": self.metadata,
        }


@dataclass
class HandoffResult:
    """Outcome of a handoff attempt.

    Attributes:
        handoff_id: The handoff event ID
        status: ACCEPTED / REJECTED / TIMEOUT / FALLBACK / ERROR
        source_minister: Who initiated the handoff
        target_minister: Who received (or was supposed to receive) the handoff
        context: The context at the time of handoff completion
        accepted: Shorthand for status == ACCEPTED
        rejection_reason: Why the handoff was rejected (if applicable)
        fallback_applied: Which fallback strategy was used
        duration_ms: How long the handoff took
        chain_snapshot: The full chain at the time of this handoff
    """
    handoff_id: str = ""
    status: HandoffStatus = HandoffStatus.ACCEPTED
    source_minister: str = ""
    target_minister: str = ""
    context: Optional[HandoffContext] = None
    rejection_reason: str = ""
    fallback_applied: str = ""
    duration_ms: float = 0.0
    chain_snapshot: List[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.status == HandoffStatus.ACCEPTED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "status": self.status.value,
            "accepted": self.accepted,
            "source_minister": self.source_minister,
            "target_minister": self.target_minister,
            "context": self.context.to_dict() if self.context else None,
            "rejection_reason": self.rejection_reason,
            "fallback_applied": self.fallback_applied,
            "duration_ms": self.duration_ms,
            "chain_snapshot": self.chain_snapshot,
        }


# ══════════════════════════════════════════════════════════════════
# HandoffProtocol
# ══════════════════════════════════════════════════════════════════


class HandoffProtocol:
    """Central orchestrator for Multi-Agent Handoff.

    Manages the full lifecycle of a handoff:
        1. Validate request (target exists, context is valid, priority ok)
        2. Execute handoff (call target callback, enforce timeout)
        3. Fallback handling (retry, try next, delegate, reject)
        4. Chain tracking (record every handoff in persistent history)
        5. Audit trail (immutable log of all handoff events)

    Attributes:
        _history: In-memory handoff history (handoff_id → HandoffResult)
        _active_handoffs: Currently in-flight handoff IDs
        _target_callbacks: Dict of minister_name → callback(target, context)
        _lock: Thread-safety lock
        max_chain_length: Maximum handoff chain depth before forced rejection
    """

    MAX_CHAIN_LENGTH = 20
    MAX_HISTORY_SIZE = 500

    def __init__(
        self,
        max_chain_length: int = 20,
        audit_logger: Any = None,
    ):
        self._history: Dict[str, HandoffResult] = {}
        self._active_handoffs: Dict[str, HandoffRequest] = {}
        self._target_callbacks: Dict[str, Callable] = {}
        self._lock = threading.Lock()
        self.max_chain_length = max_chain_length
        self._audit_logger = audit_logger

    # ── Target Registration ──────────────────────────────────────

    def register_target(
        self,
        minister_name: str,
        callback: Callable[[HandoffRequest], HandoffResult],
    ) -> None:
        """Register a minister as a potential handoff target.

        Args:
            minister_name: Name of the minister.
            callback: Function that receives a HandoffRequest and returns
                      a HandoffResult (accept/reject/timeout).
        """
        with self._lock:
            self._target_callbacks[minister_name] = callback
        logger.info(
            "Handoff target registered: %s (total targets: %d)",
            minister_name, len(self._target_callbacks),
        )

    def unregister_target(self, minister_name: str) -> bool:
        """Remove a minister from handoff targets."""
        with self._lock:
            if minister_name in self._target_callbacks:
                del self._target_callbacks[minister_name]
                return True
            return False

    def has_target(self, minister_name: str) -> bool:
        """Check if a minister is registered as a handoff target."""
        return minister_name in self._target_callbacks

    def list_targets(self) -> List[str]:
        """List all registered handoff target ministers."""
        return sorted(self._target_callbacks.keys())

    # ── Handoff Execution ────────────────────────────────────────

    def handoff(self, request: HandoffRequest) -> HandoffResult:
        """Execute a handoff from source → target minister.

        Full lifecycle:
            1. Validate the request
            2. Check chain depth limit
            3. Call target minister's callback (with timeout enforcement)
            4. Apply fallback strategy on failure
            5. Record in history and return result
        """
        t0 = time.perf_counter()

        # ── Validate ──
        validation_error = self._validate_request(request)
        if validation_error:
            result = HandoffResult(
                handoff_id=request.handoff_id,
                status=HandoffStatus.ERROR,
                source_minister=request.source_minister,
                target_minister=request.target_minister,
                context=request.context,
                rejection_reason=validation_error,
                duration_ms=(time.perf_counter() - t0) * 1000,
                chain_snapshot=request.context.chain if request.context else [],
            )
            self._record(result, request)
            return result

        # ── Chain depth check ──
        if request.context and request.context.handoff_count >= self.max_chain_length:
            result = HandoffResult(
                handoff_id=request.handoff_id,
                status=HandoffStatus.REJECTED,
                source_minister=request.source_minister,
                target_minister=request.target_minister,
                context=request.context,
                rejection_reason=(
                    f"Handoff chain depth exceeded ({request.context.handoff_count} "
                    f">= max {self.max_chain_length})"
                ),
                duration_ms=(time.perf_counter() - t0) * 1000,
                chain_snapshot=request.context.chain,
            )
            self._record(result, request)
            return result

        # ── Execute ──
        with self._lock:
            self._active_handoffs[request.handoff_id] = request

        try:
            result = self._execute_handoff(request)
        finally:
            with self._lock:
                self._active_handoffs.pop(request.handoff_id, None)

        result.duration_ms = (time.perf_counter() - t0) * 1000
        if request.context:
            result.chain_snapshot = request.context.chain
            # Record this step in the context
            request.context.record_step(
                minister=request.target_minister,
                result=result.status.value,
                status=result.status.value,
            )

        self._record(result, request)
        self._audit_handoff(result, request)

        logger.info(
            "Handoff %s: %s → %s → %s (%.1fms)",
            request.handoff_id, request.source_minister,
            request.target_minister, result.status.value,
            result.duration_ms,
        )
        return result

    def _execute_handoff(self, request: HandoffRequest) -> HandoffResult:
        """Core execution: call target callback with timeout protection."""
        target_name = request.target_minister
        callback = self._target_callbacks.get(target_name)

        if callback is None:
            return self._apply_fallback(
                request,
                HandoffResult(
                    handoff_id=request.handoff_id,
                    status=HandoffStatus.REJECTED,
                    source_minister=request.source_minister,
                    target_minister=target_name,
                    context=request.context,
                    rejection_reason=f"Target minister '{target_name}' not registered",
                ),
            )

        # Execute with timeout
        try:
            deadline = request.deadline_seconds
            if deadline <= 0:
                deadline = 30.0

            # Use a simple thread-based timeout
            result_holder: List[Optional[HandoffResult]] = [None]
            exception_holder: List[Optional[Exception]] = [None]

            def _call():
                try:
                    result_holder[0] = callback(request)
                except Exception as e:
                    exception_holder[0] = e

            t = threading.Thread(target=_call, daemon=True)
            t.start()
            t.join(timeout=deadline)

            if t.is_alive():
                # Timeout
                return HandoffResult(
                    handoff_id=request.handoff_id,
                    status=HandoffStatus.TIMEOUT,
                    source_minister=request.source_minister,
                    target_minister=target_name,
                    context=request.context,
                    rejection_reason=(
                        f"Handoff to '{target_name}' timed out "
                        f"after {deadline:.1f}s"
                    ),
                )

            if exception_holder[0] is not None:
                raise exception_holder[0]

            result = result_holder[0]
            if result is None:
                result = HandoffResult(
                    handoff_id=request.handoff_id,
                    status=HandoffStatus.ERROR,
                    source_minister=request.source_minister,
                    target_minister=target_name,
                    context=request.context,
                    rejection_reason="Target callback returned None",
                )

            # If target rejected, apply fallback
            if result.status not in (HandoffStatus.ACCEPTED,):
                return self._apply_fallback(request, result)

            return result

        except Exception as e:
            logger.error(
                "Handoff to '%s' failed: %s", target_name, e
            )
            error_result = HandoffResult(
                handoff_id=request.handoff_id,
                status=HandoffStatus.ERROR,
                source_minister=request.source_minister,
                target_minister=target_name,
                context=request.context,
                rejection_reason=str(e),
            )
            return self._apply_fallback(request, error_result)

    # ── Fallback ─────────────────────────────────────────────────

    def _apply_fallback(
        self,
        request: HandoffRequest,
        failed_result: HandoffResult,
    ) -> HandoffResult:
        """Apply the configured fallback strategy."""
        strategy = request.fallback_strategy

        if strategy == FallbackStrategy.REJECT:
            return failed_result

        if strategy == FallbackStrategy.DELEGATE_TO_EMPEROR:
            return HandoffResult(
                handoff_id=request.handoff_id,
                status=HandoffStatus.FALLBACK,
                source_minister=request.source_minister,
                target_minister=request.target_minister,
                context=request.context,
                rejection_reason=failed_result.rejection_reason,
                fallback_applied="delegate_to_emperor",
                chain_snapshot=request.context.chain if request.context else [],
            )

        if strategy == FallbackStrategy.RETRY:
            # Retry once with same target
            logger.info(
                "Handoff retry: %s → %s", request.source_minister, request.target_minister,
            )
            callback = self._target_callbacks.get(request.target_minister)
            if callback is not None:
                try:
                    retry_result = callback(request)
                    if retry_result.status == HandoffStatus.ACCEPTED:
                        return retry_result
                except Exception:
                    pass

            return HandoffResult(
                handoff_id=request.handoff_id,
                status=HandoffStatus.FALLBACK,
                source_minister=request.source_minister,
                target_minister=request.target_minister,
                context=request.context,
                rejection_reason=failed_result.rejection_reason,
                fallback_applied="retry_exhausted",
                chain_snapshot=request.context.chain if request.context else [],
            )

        if strategy == FallbackStrategy.RETRY_NEXT:
            # Try each candidate in order
            for candidate in request.candidate_ministers:
                if candidate == request.target_minister:
                    continue
                callback = self._target_callbacks.get(candidate)
                if callback is None:
                    continue

                logger.info(
                    "Handoff fallback: %s → %s (candidate)",
                    request.source_minister, candidate,
                )
                try:
                    candidate_result = callback(request)
                    if candidate_result.status == HandoffStatus.ACCEPTED:
                        candidate_result.fallback_applied = f"retry_next→{candidate}"
                        return candidate_result
                except Exception:
                    continue

            return HandoffResult(
                handoff_id=request.handoff_id,
                status=HandoffStatus.FALLBACK,
                source_minister=request.source_minister,
                target_minister=request.target_minister,
                context=request.context,
                rejection_reason=failed_result.rejection_reason,
                fallback_applied="retry_next_exhausted",
                chain_snapshot=request.context.chain if request.context else [],
            )

        # Unknown strategy → reject
        return failed_result

    # ── Validation ───────────────────────────────────────────────

    def _validate_request(self, request: HandoffRequest) -> str:
        """Validate a handoff request. Returns error string or empty on success."""
        if not request.source_minister:
            return "source_minister is required"
        if not request.target_minister:
            return "target_minister is required"
        if request.source_minister == request.target_minister:
            return "source and target minister cannot be the same"
        if request.context is None:
            return "context is required"
        if not request.context.task_id:
            return "context.task_id is required"
        # Check for circular handoff
        chain = request.context.chain
        if len(chain) >= 2 and chain[-1] == request.target_minister:
            return (
                f"Circular handoff detected: target '{request.target_minister}' "
                f"already in chain as most recent"
            )
        return ""

    # ── History & Tracking ───────────────────────────────────────

    def _record(self, result: HandoffResult, request: HandoffRequest) -> None:
        """Record a handoff result in history."""
        with self._lock:
            self._history[result.handoff_id] = result
            # Trim history
            if len(self._history) > self.MAX_HISTORY_SIZE:
                oldest = sorted(self._history.keys())[:len(self._history) - self.MAX_HISTORY_SIZE]
                for k in oldest:
                    del self._history[k]

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent handoff history (newest first)."""
        with self._lock:
            sorted_ids = sorted(
                self._history.keys(),
                key=lambda hid: self._history[hid].duration_ms,
                reverse=True,
            )[-limit:]
            return [self._history[hid].to_dict() for hid in sorted_ids]

    def get_handoff(self, handoff_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific handoff result by ID."""
        result = self._history.get(handoff_id)
        return result.to_dict() if result else None

    def get_chain(self, task_id: str) -> List[Dict[str, Any]]:
        """Get the full handoff chain for a given task ID."""
        chain = []
        with self._lock:
            for result in self._history.values():
                ctx = result.context
                if ctx and ctx.task_id == task_id:
                    chain.append(result.to_dict())
        return sorted(chain, key=lambda r: r.get("duration_ms", 0))

    def get_active_handoffs(self) -> List[Dict[str, Any]]:
        """Get currently in-flight handoffs."""
        with self._lock:
            return [
                {
                    "handoff_id": req.handoff_id,
                    "source_minister": req.source_minister,
                    "target_minister": req.target_minister,
                    "context": req.context.to_dict() if req.context else None,
                }
                for req in self._active_handoffs.values()
            ]

    def stats(self) -> Dict[str, Any]:
        """Get handoff statistics."""
        with self._lock:
            total = len(self._history)
            if total == 0:
                return {
                    "total_handoffs": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "timeout": 0,
                    "fallback": 0,
                    "error": 0,
                    "acceptance_rate": 0.0,
                    "avg_duration_ms": 0.0,
                    "active": len(self._active_handoffs),
                }

            status_counts = {
                "accepted": 0, "rejected": 0, "timeout": 0,
                "fallback": 0, "error": 0,
            }
            total_duration = 0.0

            for result in self._history.values():
                status_counts[result.status.value] = status_counts.get(
                    result.status.value, 0,
                ) + 1
                total_duration += result.duration_ms

            return {
                "total_handoffs": total,
                **status_counts,
                "acceptance_rate": round(
                    status_counts["accepted"] / total * 100, 1
                ),
                "avg_duration_ms": round(total_duration / total, 1),
                "active": len(self._active_handoffs),
                "registered_targets": len(self._target_callbacks),
            }

    # ── Audit ────────────────────────────────────────────────────

    def _audit_handoff(
        self,
        result: HandoffResult,
        request: HandoffRequest,
    ) -> None:
        """Write handoff event to audit trail."""
        if self._audit_logger is None:
            return
        try:
            self._audit_logger.log(
                trace_id=result.handoff_id,
                step=0,
                phase="handoff",
                actor=f"minister:{request.source_minister}",
                action="handoff.execute",
                input_summary=(
                    f"{request.source_minister} → {request.target_minister} "
                    f"[{request.reason[:200]}]"
                ),
                output_summary=f"status={result.status.value}",
                success=result.accepted,
                error_msg=result.rejection_reason[:500] if result.rejection_reason else "",
                duration_ms=result.duration_ms,
                extra={
                    "handoff_id": result.handoff_id,
                    "source": result.source_minister,
                    "target": result.target_minister,
                    "status": result.status.value,
                    "fallback": result.fallback_applied,
                    "chain": result.chain_snapshot,
                },
            )
        except Exception as exc:
            logger.warning("Failed to write handoff audit entry: %s", exc)


# ══════════════════════════════════════════════════════════════════
# Global singleton
# ══════════════════════════════════════════════════════════════════

handoff_protocol = HandoffProtocol()
