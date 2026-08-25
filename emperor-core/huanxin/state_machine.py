"""
State Machine — LangGraph-inspired stateful task orchestration engine.

Supports conditional branching, loop-back transitions, and built-in
error recovery with retry escalation. Designed as the execution core
for Huanxin's dispatch pipeline.

Architecture:
    State             — named node with optional enter/exit callbacks
    Transition        — directed edge with condition guard and action
    StateMachine      — runtime engine: add_state/add_transition/start/trigger

Built-in workflows:
    dispatch_workflow — planning → execution → reflection → completion
    error_recovery_wf — error → diagnose → retry → escalate (3 retries)

Usage:
    from huanxin.state_machine import StateMachine, create_dispatch_workflow

    sm = create_dispatch_workflow()
    result = sm.start("planning", data={"task": "analyze data"})
    result = sm.trigger("execute", result)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("huanxin.state_machine")


# ══════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════


@dataclass
class State:
    """A named node in the state machine graph.

    Args:
        name: Unique state identifier.
        data: Arbitrary payload carried through transitions.
        on_enter: Callback invoked when this state is entered.
        on_exit: Callback invoked when this state is exited.
    """

    name: str
    data: dict = field(default_factory=dict)
    on_enter: Optional[Callable[[StateMachineContext], None]] = None
    on_exit: Optional[Callable[[StateMachineContext], None]] = None


@dataclass
class Transition:
    """A directed edge connecting two states with optional guard.

    Args:
        from_state: Source state name.
        to_state: Target state name.
        condition: Optional guard callable; only fire when it returns True.
        action: Optional callable executed during the transition.
    """

    from_state: str
    to_state: str
    condition: Optional[Callable[[StateMachineContext], bool]] = None
    action: Optional[Callable[[StateMachineContext], dict]] = None


@dataclass
class StateMachineContext:
    """Mutable context carried through the state machine lifecycle.

    Holds the current state, accumulated data, execution trace, and
    any user-defined payload.
    """

    current_state: str = ""
    data: dict = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    stopped: bool = False


# ══════════════════════════════════════════════════════════════════
# State Machine Engine
# ══════════════════════════════════════════════════════════════════


class StateMachine:
    """LangGraph-inspired state machine execution engine.

    Manages a graph of named ``State`` nodes connected by ``Transition``
    edges. Supports:
    - Conditional branching via ``condition`` guards
    - Loop-back transitions (return to a prior state)
    - On-enter / on-exit callbacks per state
    - Transition actions for side effects

    Example:
        sm = StateMachine()
        sm.add_state(State(name="idle"))
        sm.add_state(State(name="processing"))
        sm.add_transition(Transition("idle", "processing"))
        ctx = sm.start("idle")
        ctx = sm.trigger("processing", ctx)
        print(sm.current_state)  # "processing"
        print(sm.history)        # ["idle", "processing"]
    """

    HISTORY_LIMIT: int = 500

    def __init__(self, name: str = "default") -> None:
        self.name = name
        self._states: dict[str, State] = {}
        self._transitions: dict[str, list[Transition]] = {}  # from_state → [Transition]
        self._context: Optional[StateMachineContext] = None
        self._completed: bool = False

    # ── Builder API ────────────────────────────────────────────────

    def add_state(self, state: State) -> None:
        """Register a state node.

        Args:
            state: ``State`` with a unique name.
        """
        if state.name in self._states:
            logger.warning("[StateMachine] state '%s' already registered, overwriting", state.name)
        self._states[state.name] = state
        if state.name not in self._transitions:
            self._transitions[state.name] = []

    def add_transition(self, transition: Transition) -> None:
        """Register a transition edge between two states.

        Args:
            transition: ``Transition`` with from_state / to_state.
        """
        if transition.from_state not in self._transitions:
            self._transitions[transition.from_state] = []
        self._transitions[transition.from_state].append(transition)

    # ── Runtime API ────────────────────────────────────────────────

    def start(self, initial_state: str, data: Optional[dict] = None) -> StateMachineContext:
        """Start the machine at ``initial_state``.

        Resets internal context and invokes the initial state's on_enter
        callback.

        Args:
            initial_state: Name of the starting state (must be registered).
            data: Optional initial payload.

        Returns:
            ``StateMachineContext`` after entering the initial state.

        Raises:
            ValueError: If ``initial_state`` is not registered.
        """
        if initial_state not in self._states:
            raise ValueError(f"State '{initial_state}' not registered")

        self._context = StateMachineContext(
            current_state=initial_state,
            data=data or {},
        )
        self._completed = False
        self._context.history.append(initial_state)

        self._invoke_on_enter(initial_state)
        return self._context

    def trigger(
        self,
        target_state: str,
        context: Optional[StateMachineContext] = None,
    ) -> StateMachineContext:
        """Transition from current state to ``target_state``.

        Evaluates transitions from the current state. Finds the first
        matching transition (condition guard passes or no guard), invokes
        the current state's on_exit, any transition action, then the
        target state's on_enter.

        Args:
            target_state: Desired next state name.
            context: Optional context; defaults to internal context.

        Returns:
            Updated ``StateMachineContext``.

        Raises:
            ValueError: If no valid transition exists.
        """
        ctx = context or self._context
        if ctx is None:
            raise RuntimeError("StateMachine not started; call start() first")

        if ctx.stopped:
            logger.debug("[StateMachine] machine is stopped, ignoring trigger to '%s'", target_state)
            return ctx

        current = ctx.current_state

        # Find matching transition
        candidates = self._transitions.get(current, [])
        matched: Optional[Transition] = None
        for t in candidates:
            if t.to_state != target_state:
                continue
            if t.condition is None or t.condition(ctx):
                matched = t
                break

        if matched is None:
            raise ValueError(
                f"No valid transition from '{current}' to '{target_state}'"
            )

        # Execute exit callback
        self._invoke_on_exit(current)

        # Execute transition action
        if matched.action is not None:
            try:
                action_result = matched.action(ctx)
                if isinstance(action_result, dict):
                    ctx.data.update(action_result)
            except Exception as exc:
                logger.exception("[StateMachine] transition action failed: %s", exc)
                ctx.errors.append(f"TRANSITION_ERROR({current}→{target_state}): {exc}")

        # Update context
        ctx.current_state = target_state
        ctx.history.append(target_state)

        # Trim history
        if len(ctx.history) > self.HISTORY_LIMIT:
            ctx.history = ctx.history[-self.HISTORY_LIMIT:]

        # Execute enter callback
        self._invoke_on_enter(target_state)

        return ctx

    def stop(self) -> None:
        """Stop the state machine; subsequent trigger calls are no-ops."""
        if self._context is not None:
            self._context.stopped = True
        self._completed = True

    def reset(self) -> None:
        """Reset internal context; machine must be started again."""
        self._context = None
        self._completed = False

    # ── Query API ──────────────────────────────────────────────────

    @property
    def current_state(self) -> Optional[str]:
        """Name of the current state, or None if not started."""
        if self._context is None:
            return None
        return self._context.current_state

    @property
    def history(self) -> list[str]:
        """Ordered list of state names visited in this run."""
        if self._context is None:
            return []
        return list(self._context.history)

    @property
    def completed(self) -> bool:
        """Whether the machine has been stopped."""
        return self._completed

    def get_transitions_from(self, state_name: str) -> list[Transition]:
        """Return all transitions whose ``from_state`` matches."""
        return list(self._transitions.get(state_name, []))

    def get_state(self, state_name: str) -> Optional[State]:
        """Return the registered ``State`` or None."""
        return self._states.get(state_name)

    @property
    def states(self) -> dict[str, State]:
        """Return a copy of all registered states."""
        return dict(self._states)

    def to_dict(self) -> dict[str, Any]:
        """Export machine graph as dict."""
        nodes = {
            name: {"data": s.data}
            for name, s in self._states.items()
        }
        edges = []
        for from_s, trans_list in self._transitions.items():
            for t in trans_list:
                edges.append({
                    "from": t.from_state,
                    "to": t.to_state,
                    "has_condition": t.condition is not None,
                    "has_action": t.action is not None,
                })
        return {
            "name": self.name,
            "nodes": nodes,
            "edges": edges,
            "current_state": self.current_state,
            "history": self.history,
            "completed": self.completed,
        }

    # ── Internal helpers ───────────────────────────────────────────

    def _invoke_on_enter(self, state_name: str) -> None:
        state = self._states.get(state_name)
        if state is None or state.on_enter is None:
            return
        try:
            state.on_enter(self._context)
        except Exception as exc:
            logger.exception("[StateMachine] on_enter callback failed for '%s': %s", state_name, exc)

    def _invoke_on_exit(self, state_name: str) -> None:
        state = self._states.get(state_name)
        if state is None or state.on_exit is None:
            return
        try:
            state.on_exit(self._context)
        except Exception as exc:
            logger.exception("[StateMachine] on_exit callback failed for '%s': %s", state_name, exc)


# ══════════════════════════════════════════════════════════════════
# Built-in Workflow Templates
# ══════════════════════════════════════════════════════════════════


def create_dispatch_workflow() -> StateMachine:
    """Create a planning→execution→reflection→completion dispatch workflow.

    This is the standard Huanxin task execution pipeline:
      1. planning    — agent formulates an execution plan
      2. execution   — agent performs the actual work
      3. reflection  — self-reflection quality check (Reflexion)
      4. completion  — finalize and return result

    Loop-back: if reflection confidence < threshold, loop back to
    execution for one more attempt (max 3 total loops tracked via
    metadata loop_count).
    """
    sm = StateMachine(name="dispatch_workflow")

    # States
    sm.add_state(State(
        name="planning",
        data={"phase": "plan"},
        on_enter=lambda ctx: logger.info("[DispatchWorkflow] entering planning phase"),
    ))
    sm.add_state(State(
        name="execution",
        data={"phase": "execute"},
        on_enter=lambda ctx: logger.info("[DispatchWorkflow] entering execution phase"),
    ))
    sm.add_state(State(
        name="reflection",
        data={"phase": "reflect"},
        on_enter=lambda ctx: logger.info("[DispatchWorkflow] entering reflection phase"),
    ))
    sm.add_state(State(
        name="completion",
        data={"phase": "complete"},
        on_enter=lambda ctx: logger.info("[DispatchWorkflow] entering completion phase"),
    ))

    # Forward transitions
    sm.add_transition(Transition("planning", "execution"))
    sm.add_transition(Transition("execution", "reflection"))
    sm.add_transition(Transition("reflection", "completion"))

    # Loop-back: reflection → execution (conditional on confidence)
    def _should_retry(ctx: StateMachineContext) -> bool:
        confidence = ctx.data.get("confidence", 1.0)
        loop_count = ctx.metadata.get("loop_count", 0)
        max_loops = ctx.metadata.get("max_loops", 3)
        return confidence < 0.6 and loop_count < max_loops

    def _loop_action(ctx: StateMachineContext) -> dict:
        ctx.metadata["loop_count"] = ctx.metadata.get("loop_count", 0) + 1
        logger.info(
            "[DispatchWorkflow] reflexion loop-back #%d (confidence=%.4f)",
            ctx.metadata["loop_count"], ctx.data.get("confidence", 0),
        )
        return {"retry_attempt": ctx.metadata["loop_count"]}

    sm.add_transition(Transition(
        "reflection", "execution",
        condition=_should_retry,
        action=_loop_action,
    ))

    return sm


def create_error_recovery_workflow() -> StateMachine:
    """Create an error → diagnose → retry → escalate recovery workflow.

    Handles failures with up to 3 retries before escalating.

    Flow:
      1. error      — failure detected, capture context
      2. diagnose   — analyze root cause
      3. retry      — attempt recovery (loop back to diagnose if still failing)
      4. escalate   — signal for human / higher-level intervention
    """
    sm = StateMachine(name="error_recovery_workflow")

    sm.add_state(State(
        name="error",
        data={"phase": "error"},
        on_enter=lambda ctx: logger.warning("[ErrorRecovery] error state entered"),
    ))
    sm.add_state(State(
        name="diagnose",
        data={"phase": "diagnose"},
        on_enter=lambda ctx: logger.info("[ErrorRecovery] diagnosing failure"),
    ))
    sm.add_state(State(
        name="retry",
        data={"phase": "retry"},
        on_enter=lambda ctx: logger.info("[ErrorRecovery] attempting retry"),
    ))
    sm.add_state(State(
        name="escalate",
        data={"phase": "escalate"},
        on_enter=lambda ctx: logger.error("[ErrorRecovery] escalating after max retries"),
    ))

    # Forward transitions
    sm.add_transition(Transition("error", "diagnose"))

    def _retry_action(ctx: StateMachineContext) -> dict:
        ctx.metadata["retry_count"] = ctx.metadata.get("retry_count", 0) + 1
        logger.info("[ErrorRecovery] retry #%d", ctx.metadata["retry_count"])
        return {"retry_attempt": ctx.metadata["retry_count"]}

    sm.add_transition(Transition("diagnose", "retry", action=_retry_action))

    # Escalate condition: retries exhausted
    def _should_escalate(ctx: StateMachineContext) -> bool:
        return ctx.metadata.get("retry_count", 0) >= ctx.metadata.get("max_retries", 3)

    sm.add_transition(Transition("retry", "escalate", condition=_should_escalate))

    # Loop-back: retry → diagnose (if still retrying)
    def _should_retry_diagnose(ctx: StateMachineContext) -> bool:
        return ctx.metadata.get("retry_count", 0) < ctx.metadata.get("max_retries", 3)

    sm.add_transition(Transition("retry", "diagnose", condition=_should_retry_diagnose))

    return sm


# ══════════════════════════════════════════════════════════════════
# Workflow registry
# ══════════════════════════════════════════════════════════════════

_WORKFLOW_TEMPLATES: dict[str, Callable[[], dict]] = {
    "dispatch_workflow": lambda: create_dispatch_workflow().to_dict(),
    "error_recovery_workflow": lambda: create_error_recovery_workflow().to_dict(),
}


def list_workflow_templates() -> list[dict[str, Any]]:
    """Return metadata for all built-in workflow templates."""
    return [
        {
            "name": name,
            "description": _describe_workflow(name),
        }
        for name in _WORKFLOW_TEMPLATES
    ]


def get_workflow_template(name: str) -> Optional[dict[str, Any]]:
    """Return the graph dict for a named workflow template, or None."""
    factory = _WORKFLOW_TEMPLATES.get(name)
    if factory is None:
        return None
    return factory()


def execute_workflow(
    name: str,
    initial_data: Optional[dict] = None,
    max_loops: int = 3,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Execute a named workflow from start to completion.

    Args:
        name: Template name ('dispatch_workflow' or 'error_recovery_workflow').
        initial_data: Optional payload for the initial state.
        max_loops: Max reflexion loops (dispatch_workflow only).
        max_retries: Max retry attempts (error_recovery_workflow only).

    Returns:
        Dict with status, history, final_data, errors.
    """
    if name == "dispatch_workflow":
        sm = create_dispatch_workflow()
        ctx = sm.start("planning", data=initial_data or {})
        ctx.metadata["max_loops"] = max_loops
        ctx.metadata["loop_count"] = 0

        # planning → execution
        ctx = sm.trigger("execution", ctx)
        # execution → reflection
        ctx = sm.trigger("reflection", ctx)

        # If confidence is low, loop: reflection → execution → reflection
        while (
            ctx.data.get("confidence", 1.0) < 0.6
            and ctx.metadata.get("loop_count", 0) < max_loops
        ):
            ctx = sm.trigger("execution", ctx)
            ctx = sm.trigger("reflection", ctx)

        # reflection → completion
        ctx = sm.trigger("completion", ctx)
        sm.stop()

        return {
            "workflow": name,
            "status": "completed",
            "history": list(ctx.history),
            "data": ctx.data,
            "errors": list(ctx.errors),
            "metadata": ctx.metadata,
        }

    elif name == "error_recovery_workflow":
        sm = create_error_recovery_workflow()
        ctx = sm.start("error", data=initial_data or {})
        ctx.metadata["max_retries"] = max_retries
        ctx.metadata["retry_count"] = 0

        # error → diagnose
        ctx = sm.trigger("diagnose", ctx)

        # diagnose → retry
        ctx = sm.trigger("retry", ctx)

        # retry → escalate (if exhausted) or retry → diagnose → retry
        while ctx.metadata.get("retry_count", 0) < max_retries:
            # Decision: if error is resolved, we'd stop; for demo, retry exhausts
            if ctx.metadata.get("retry_count", 0) >= max_retries:
                break
            ctx = sm.trigger("diagnose", ctx)
            ctx = sm.trigger("retry", ctx)

        # Final: retry → escalate
        ctx = sm.trigger("escalate", ctx)
        sm.stop()

        return {
            "workflow": name,
            "status": "escalated",
            "history": list(ctx.history),
            "data": ctx.data,
            "errors": list(ctx.errors),
            "metadata": ctx.metadata,
        }

    else:
        raise ValueError(f"Unknown workflow template: '{name}'")


def _describe_workflow(name: str) -> str:
    descriptions = {
        "dispatch_workflow": (
            "Standard Huanxin task dispatch pipeline: "
            "planning → execution → reflection → completion, "
            "with reflexion loop-back when confidence < threshold."
        ),
        "error_recovery_workflow": (
            "Error recovery pipeline: "
            "error → diagnose → retry → escalate, "
            "with up to 3 retry attempts before escalation."
        ),
    }
    return descriptions.get(name, "Built-in workflow template.")
