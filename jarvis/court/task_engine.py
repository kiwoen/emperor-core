"""TaskEngine — LLM-backed task execution & feedback loop.

TaskEngine bridges the evolutionary court with real LLM calls:
    - Accepts task schemas
    - Routes to the best-match minister
    - Executes with configurable LLM backends
    - Records outcomes → merit feedback
    - Supports batch/submit-and-poll patterns

Usage:
    from jarvis.court.court import Court
    from jarvis.court.task_engine import TaskEngine, TaskRequest, TaskOutcome

    court = Court()
    court.register("turing", domain="math")
    engine = TaskEngine(court)

    req = TaskRequest(
        id="q1",
        prompt="What is 17 * 23?",
        domain="math",
    )
    outcome = engine.execute(req)
    print(outcome.success, outcome.merit_score)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional, Protocol

# ══════════════════════════════════════════════════════════════════
# Core types
# ══════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)


class TaskState(Enum):
    PENDING = auto()
    DISPATCHED = auto()
    COMPLETED = auto()
    FAILED = auto()


# ── Prototypes ────────────────────────────────────────────────────


class LLMBackend(Protocol):
    """Callable that takes (prompt: str, **kwargs) → str."""

    def __call__(self, prompt: str, **kwargs: Any) -> str: ...


# ── Data types ────────────────────────────────────────────────────


@dataclass
class TaskRequest:
    """A task submitted to the engine."""

    id: str  # unique task identifier
    prompt: str
    domain: str = "general"
    expected: Optional[str] = None  # optional answer for auto-scoring
    deadline_seconds: float = 30.0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskOutcome:
    """Result of a single task execution."""

    task_id: str
    state: TaskState
    minister: str  # assigned minister name
    raw_response: str = ""
    success: bool = False
    confidence: float = 0.0
    execution_time_ms: float = 0.0
    merit_score: float = 0.0
    error: Optional[str] = None
    capability_name: str = ""  # matched capability (for audit)
    capability_result: str = ""  # capability execution result (for audit)


# ══════════════════════════════════════════════════════════════════
# Built-in scoring
# ══════════════════════════════════════════════════════════════════


def _simple_confidence(response: str, expected: Optional[str]) -> float:
    """0.3 – 0.95 heuristic based on length and correctness."""
    base = 0.3
    if not response.strip():
        return 0.1

    length_bonus = min(len(response) / 2000.0, 0.3)
    base += length_bonus

    if expected is not None and expected.strip():
        if expected.strip().lower() in response.strip().lower():
            base += 0.35
        else:
            base -= 0.15

    return max(0.0, min(base, 0.95))


# ══════════════════════════════════════════════════════════════════
# TaskEngine
# ══════════════════════════════════════════════════════════════════


class TaskEngine:
    """Routes tasks to ministers, executes via LLM, records outcomes."""

    def __init__(
        self,
        court: Any,  # Court
        llm: Optional[LLMBackend] = None,
        scorer: Optional[Callable[[str, Optional[str]], float]] = None,
        capability_registry: Optional[Any] = None,
    ):
        self._court = court
        self._llm = llm or _default_llm_backend
        self._scorer = scorer or _simple_confidence
        self._capability_registry = capability_registry  # CapabilityRegistry instance

        self._outcomes: list[TaskOutcome] = []
        self._pending: dict[str, TaskRequest] = {}

    # ── Properties ─────────────────────────────────────────────────

    @property
    def outcomes(self) -> list[TaskOutcome]:
        return list(self._outcomes)

    @property
    def success_rate(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for o in self._outcomes if o.success) / len(self._outcomes)

    @property
    def total_tasks(self) -> int:
        return len(self._outcomes) + len(self._pending)

    # ── Task lifecycle ─────────────────────────────────────────────

    def submit(self, request: TaskRequest) -> str:
        """Submit a task for later execution."""
        if request.id in self._pending:
            raise ValueError(f"Task '{request.id}' already pending")
        self._pending[request.id] = request
        logger.debug("[TaskEngine] Submitted '%s'", request.id)
        return request.id

    def execute(
        self,
        request: TaskRequest,
        *,
        minister: Optional[str] = None,
    ) -> TaskOutcome:
        """Pick a minister, run the prompt, score, and feed back."""
        start = time.perf_counter()

        # 1. Select minister
        if minister is None:
            minister = self._select_minister(request.domain)

        # 2. Build genome-aware parameters
        genome_params = self._get_genome_params(minister)

        # 3. Run LLM
        try:
            raw = self._llm(request.prompt, **genome_params)
            state = TaskState.COMPLETED
            error = None
        except Exception as exc:
            raw = ""
            state = TaskState.FAILED
            error = str(exc)

        elapsed_ms = (time.perf_counter() - start) * 1000

        # 3b. Capability execution — if registry exists, try to augment with real data
        capability_name = ""
        capability_result_str = ""
        capability_output = ""
        if self._capability_registry is not None:
            try:
                # Determine domain — prefer request domain, then genome domain
                exec_domain = request.domain
                try:
                    genome = self._court._sm._genomes.get(minister)
                    if genome:
                        exec_domain = genome.domain
                except Exception:
                    pass

                best_cap = self._capability_registry.find_best(request.prompt, exec_domain)
                if best_cap is not None:
                    cap_result = self._capability_registry.execute(
                        best_cap.name, request.prompt
                    )
                    capability_output = (
                        f"\n\n[能力结果: {best_cap.name}]\n{cap_result['result']}"
                    )
                    capability_name = best_cap.name
                    capability_result_str = str(cap_result.get("data", cap_result))
                    logger.debug(
                        "[TaskEngine] Capability '%s' executed for task '%s'",
                        best_cap.name,
                        request.id,
                    )
            except Exception as exc:
                logger.debug(
                    "[TaskEngine] Capability execution skipped for '%s': %s",
                    request.id,
                    exc,
                )
                capability_output = ""

        # Combine raw LLM output with capability output
        combined_response = raw + capability_output

        # 4. Score
        confidence = self._scorer(combined_response, request.expected)
        success = state == TaskState.COMPLETED and confidence > 0.3

        merit = confidence * 100

        outcome = TaskOutcome(
            task_id=request.id,
            state=state,
            minister=minister,
            raw_response=combined_response,
            success=success,
            confidence=round(confidence, 4),
            execution_time_ms=round(elapsed_ms, 1),
            merit_score=round(merit, 2),
            error=error,
            capability_name=capability_name,
            capability_result=capability_result_str,
        )

        self._outcomes.append(outcome)

        # 5. Apply task feedback to minister's genome (streaks, hits, etc.)
        try:
            capability_name = None
            if self._capability_registry is not None:
                try:
                    exec_domain = request.domain
                    genome = self._court._sm._genomes.get(minister)
                    if genome:
                        exec_domain = genome.domain
                    best_cap = self._capability_registry.find_best(
                        request.prompt, exec_domain
                    )
                    if best_cap is not None:
                        capability_name = best_cap.name
                except Exception:
                    pass
            self._apply_task_feedback(minister, capability_name)
        except Exception:
            pass

        # 5b. Publish task_completed event for SSE dashboard
        try:
            from jarvis.event_bus import event_bus, Event
            result_text = outcome.get("result", "") or ""
            event_bus.publish(Event("task_completed", {
                "minister": minister,
                "domain": request.domain,
                "capability": capability_name or "",
                "result_preview": result_text[:100],
            }))
        except Exception:
            pass

        # 6. Feed back to merit board
        try:
            self._court.record_dispatch(
                minister=minister,
                edict_id=request.id,
                intent=request.prompt[:80],
                success=success,
                confidence=confidence,
                execution_time_ms=elapsed_ms,
            )
        except Exception:
            pass

        # 6. Record feedback score
        try:
            self._court.record_feedback(
                minister=minister,
                edict_id=request.id,
                score=merit,
            )
        except Exception:
            pass

        logger.info(
            "[TaskEngine] '%s' → %s (%.0fms, merit=%.1f)",
            request.id,
            minister,
            elapsed_ms,
            merit,
        )
        return outcome

    def execute_batch(
        self,
        requests: list[TaskRequest],
    ) -> list[TaskOutcome]:
        """Execute multiple tasks sequentially."""
        return [self.execute(r) for r in requests]

    def summary(self) -> dict:
        """Human-readable engine summary."""
        return {
            "total_tasks": self.total_tasks,
            "completed": sum(
                1 for o in self._outcomes
                if o.state == TaskState.COMPLETED
            ),
            "failed": sum(
                1 for o in self._outcomes
                if o.state == TaskState.FAILED
            ),
            "success_rate": round(self.success_rate, 3),
            "avg_merit": (
                round(
                    sum(o.merit_score for o in self._outcomes)
                    / len(self._outcomes),
                    2,
                )
                if self._outcomes
                else 0.0
            ),
        }

    # ── Internals ─────────────────────────────────────────────────

    def _select_minister(self, domain: str) -> str:
        """Pick the best-fit minister for a domain."""
        active = self._court.active_ministers

        if not active:
            raise RuntimeError(
                "No active ministers. Register one first: "
                "emperor register --name turing --domain math"
            )

        # Try domain match
        for name in active:
            # We can't easily access genome domain from active list,
            # so use merit ranking as a proxy
            pass

        # Fallback: pick highest-merit minister
        try:
            ranking = self._court.merit_ranking
            if ranking:
                return ranking[0].name
        except Exception:
            pass

        return active[0]

    def _get_genome_params(self, minister: str) -> dict[str, Any]:
        """Extract LLM parameters from minister's genome."""
        try:
            genome = self._court._sm._genomes.get(minister)
            if genome:
                return {
                    "temperature": genome.temperature,
                    "top_p": 0.5 + genome.exploration_rate * 0.5,
                    "presence_penalty": genome.exploration_rate,
                    "frequency_penalty": genome.conservatism,
                }
        except Exception:
            pass
        return {"temperature": 0.7}

    def _apply_task_feedback(
        self, minister: str, capability_name: str | None = None
    ) -> None:
        """Update minister genome stats based on task execution.

        Tracks success/failure streaks, total tasks, and capability hits.
        Adjusts merit (via MeritBoard proxy) and stability (via confidence_baseline).
        """
        genome = None
        try:
            genome = self._court._sm._genomes.get(minister)
        except Exception:
            return
        if genome is None:
            return

        genome.total_tasks += 1

        if capability_name:
            # Matched a real capability → high-quality execution
            genome.capability_hits += 1
            genome.success_streak += 1
            genome.failure_streak = 0

            # merit gain: base +2, plus streak bonus
            streak_bonus = min(genome.success_streak // 3, 3)
            merit_gain = 2 + streak_bonus

            # Apply merit via MeritBoard if available
            if self._court._merit_board:
                try:
                    current = self._court._merit_board.compute_merit(minister)
                    new_merit = min(current + merit_gain, 100.0)
                    # Record a feedback entry to nudge merit
                    self._court.record_feedback(
                        minister=minister,
                        edict_id=f"feedback-{genome.total_tasks}",
                        score=new_merit,
                    )
                except Exception:
                    pass

            # stability micro-increase
            genome.confidence_baseline = min(
                genome.confidence_baseline + 0.01, 1.0
            )
        else:
            # No capability matched → simulated result, lower quality
            genome.failure_streak += 1
            genome.success_streak = 0

            # Mild stability decrease
            genome.confidence_baseline = max(
                genome.confidence_baseline - 0.005, 0.0
            )

        # If failure streak is long, stability drops faster
        if genome.failure_streak >= 10:
            genome.confidence_baseline = max(
                genome.confidence_baseline - 0.02, 0.0
            )

        # stability < 0.3 triggers merit penalty
        if genome.confidence_baseline < 0.3:
            if self._court._merit_board:
                try:
                    current = self._court._merit_board.compute_merit(minister)
                    new_merit = max(current - 3, 0.0)
                    self._court.record_feedback(
                        minister=minister,
                        edict_id=f"penalty-{genome.total_tasks}",
                        score=new_merit,
                    )
                except Exception:
                    pass


# ══════════════════════════════════════════════════════════════════
# Default backends
# ══════════════════════════════════════════════════════════════════


def _default_llm_backend(prompt: str, **kwargs: Any) -> str:
    """Mock LLM backend (logs prompt, returns placeholder)."""
    logger.debug(
        "[TaskEngine] mock backend called with prompt=%r, kwargs=%r",
        prompt[:100],
        kwargs,
    )
    temperature = kwargs.get("temperature", 0.7)
    if temperature < 0.3:
        return f"[cold-answer] {_deterministic_reply(prompt)}"
    return f"[mock-response] Understood: '{prompt[:80]}...'"


def _deterministic_reply(prompt: str) -> str:
    """Simple deterministic reply for cold-temperature tests."""
    if "17 * 23" in prompt or "17*23" in prompt:
        return "391"
    if "capital of" in prompt.lower() and "france" in prompt.lower():
        return "Paris"
    if "hello" in prompt.lower():
        return "Hello! How can I help you?"
    return f"Acknowledged: {prompt[:50]}"
