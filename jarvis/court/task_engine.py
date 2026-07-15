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
    ):
        self._court = court
        self._llm = llm or _default_llm_backend
        self._scorer = scorer or _simple_confidence

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

        # 4. Score
        confidence = self._scorer(raw, request.expected)
        success = state == TaskState.COMPLETED and confidence > 0.3

        merit = confidence * 100

        outcome = TaskOutcome(
            task_id=request.id,
            state=state,
            minister=minister,
            raw_response=raw,
            success=success,
            confidence=round(confidence, 4),
            execution_time_ms=round(elapsed_ms, 1),
            merit_score=round(merit, 2),
            error=error,
        )

        self._outcomes.append(outcome)

        # 5. Feed back to merit board
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
