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

from jarvis.court.fitness import FitnessSignal, RealTaskFitness

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
    """DEPRECATED length-based heuristic — kept only for regression tests.

    .. deprecated:: P0.3
        This scorer awarded up to +0.30 for ``len(response) / 2000``, which
        turned the evolutionary merit signal into a verbosity contest (classic
        reward hacking).  :class:`jarvis.court.fitness.RealTaskFitness` is now
        the :class:`TaskEngine` default.  **Do not wire this into new code.**

    Returns:
        A 0.1 – 0.95 heuristic score based on length and expected-answer match.
    """
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
        router: Optional[Any] = None,  # SmartRouter
    ):
        self._court = court
        self._llm = llm or _default_llm_backend
        # P0.3: the default fitness signal is now derived from real task
        # outcomes (execution success + unit-test pass rate), not from how
        # many characters the model happened to emit.
        self._scorer = scorer or RealTaskFitness()
        self._capability_registry = capability_registry  # CapabilityRegistry instance
        self._smart_router: Optional[Any] = router  # P0.4 — set via set_router()

        self._outcomes: list[TaskOutcome] = []
        self._pending: dict[str, TaskRequest] = {}

    # ── Router wiring (P0.4) ───────────────────────────────────────

    def set_router(self, router: Optional[Any]) -> None:
        """Attach (or detach) the SmartRouter used for minister selection.

        Passing ``None`` is explicitly supported and means "routing is
        unavailable" — :meth:`_select_minister` then degrades to plain
        domain-string matching plus merit ranking.  The caller is expected to
        have already logged *why* the router is missing.
        """
        self._smart_router = router
        if router is None:
            logger.warning(
                "[TaskEngine] 未接入 SmartRouter，选臣退化为域名字符串匹配 + 功勋排序"
            )
        else:
            logger.info(
                "[TaskEngine] SmartRouter 已接入：%s", type(router).__name__
            )

    @property
    def router(self) -> Optional[Any]:
        """The attached SmartRouter, or ``None`` when routing is unavailable."""
        return self._smart_router

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
            minister = self._select_minister(request.domain, request.prompt)

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

        # 4. Score — P0.3: fitness comes from the real execution outcome.
        confidence = self._score(
            response=combined_response,
            expected=request.expected,
            executed_ok=state == TaskState.COMPLETED,
            error=error,
            domain=request.domain,
            test_pass_rate=request.meta.get("test_pass_rate"),
        )
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
            # NOTE: this used to call ``outcome.get("result", "")``.  TaskOutcome
            # is a dataclass, not a dict, so it raised AttributeError on every
            # single task and the bare ``except`` below swallowed it — meaning
            # the dashboard never received a task_completed event.
            result_text = outcome.raw_response or ""
            event_bus.publish(Event("task_completed", {
                "minister": minister,
                "domain": request.domain,
                "capability": capability_name or "",
                "success": outcome.success,
                "confidence": outcome.confidence,
                "result_preview": result_text[:100],
            }))
        except Exception:
            logger.debug(
                "[TaskEngine] task_completed 事件发布失败 (task=%s)",
                request.id, exc_info=True,
            )

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

    def _score(
        self,
        *,
        response: str,
        expected: Optional[str],
        executed_ok: bool,
        error: Optional[str],
        domain: str,
        test_pass_rate: Optional[float] = None,
    ) -> float:
        """Run the configured scorer, adapting to its supported signature.

        :class:`~jarvis.court.fitness.RealTaskFitness` (the default) receives a
        full :class:`FitnessSignal` so it can see whether the task *actually*
        succeeded.  A user-supplied legacy scorer with the historical
        ``(response, expected)`` signature still works unchanged.
        """
        signal = FitnessSignal(
            execution_success=executed_ok,
            test_pass_rate=test_pass_rate,
            response=response,
            expected=expected,
            error=error,
            domain=domain,
        )

        scorer = self._scorer
        if hasattr(scorer, "score") and callable(getattr(scorer, "score")):
            try:
                return float(scorer.score(signal))
            except Exception:
                logger.warning(
                    "[TaskEngine] scorer.score() 失败，回退到旧式两参调用",
                    exc_info=True,
                )

        try:
            return float(scorer(response, expected))
        except Exception:
            logger.error(
                "[TaskEngine] scorer 调用失败，本次任务置信度记为 0.0",
                exc_info=True,
            )
            return 0.0

    def _select_minister(self, domain: str, prompt: str = "") -> str:
        """Pick the best-fit minister for a task (P0.4 / P0.5).

        Selection is a three-tier preference, each tier internally ordered by
        merit (highest first):

            1. **Exact domain match** — the minister's genome domain equals the
               requested domain.
            2. **Capability match** — the minister's domain and the request map
               to the same :class:`~jarvis.model_router.Capability` according to
               the attached ``SmartRouter``.  This is what lets a ``science``
               minister pick up a ``math`` task.
            3. **Merit fallback** — no domain signal at all, so the
               highest-merit active minister takes the task.

        Previously this method contained a ``for name in active: pass`` loop,
        i.e. routing was decorative: every task always fell through to
        "highest merit". That is now a real, tested selection path.

        Args:
            domain: The requested task domain.
            prompt: The task prompt, used by the router for classification.

        Returns:
            The name of the selected minister.

        Raises:
            RuntimeError: If the court has no active ministers.
        """
        active = self._court.active_ministers

        if not active:
            raise RuntimeError(
                "No active ministers. Register one first: "
                "emperor register --name turing --domain math"
            )

        merits = self._merit_map()
        requested = (domain or "general").strip().lower()

        exact: list[str] = []
        by_capability: list[str] = []

        target_cap = self._classify_request(prompt, requested)

        for name in active:
            minister_domain = self._minister_domain(name)
            if minister_domain and minister_domain == requested:
                exact.append(name)
                continue
            if (
                target_cap is not None
                and minister_domain
                and self._classify_domain(minister_domain) == target_cap
            ):
                by_capability.append(name)

        for tier_name, tier in (("domain", exact), ("capability", by_capability)):
            if not tier:
                continue
            chosen = max(tier, key=lambda n: merits.get(n, 0.0))
            logger.debug(
                "[TaskEngine] 选臣命中 %s 匹配：domain=%s → %s (候选 %s)",
                tier_name, requested, chosen, tier,
            )
            return chosen

        # Tier 3 — no domain signal: fall back to the highest-merit minister.
        ranked_active = [n for n in active if n in merits]
        if ranked_active:
            chosen = max(ranked_active, key=lambda n: merits[n])
            logger.debug(
                "[TaskEngine] 选臣无领域命中，回退功勋第一：domain=%s → %s",
                requested, chosen,
            )
            return chosen

        logger.debug(
            "[TaskEngine] 选臣无领域命中且无功勋数据，取首位活跃大臣：%s", active[0]
        )
        return active[0]

    # Attribute names used by the various merit report dataclasses.
    # ``SlidingMeritReport`` / ``MeritReport`` expose ``minister`` +
    # ``merit_score``.  The old code here read ``entry.name``, which does not
    # exist on either — it raised AttributeError on every call and was
    # swallowed by a bare ``except``, so the "merit fallback" never ran.
    _MERIT_NAME_ATTRS = ("minister", "name", "minister_name")
    _MERIT_SCORE_ATTRS = ("merit_score", "merit", "windowed_merit", "score")

    def _merit_map(self) -> dict[str, float]:
        """Return ``{minister_name: merit}`` for every ranked minister.

        Ministers with no dispatch history simply do not appear in the merit
        ranking; callers must treat a missing key as "no merit signal" rather
        than as zero merit.

        Returns an empty mapping when the merit board is unavailable, so
        selection degrades gracefully instead of crashing task execution.
        """
        try:
            ranking = self._court.merit_ranking or []
        except Exception:
            logger.warning(
                "[TaskEngine] 无法读取功勋排行，选臣将忽略功勋权重", exc_info=True
            )
            return {}

        merits: dict[str, float] = {}
        for entry in ranking:
            name = self._first_attr(entry, self._MERIT_NAME_ATTRS)
            if not name:
                logger.debug(
                    "[TaskEngine] 功勋条目缺少大臣名字段，已跳过：%r", entry
                )
                continue
            score = self._first_attr(entry, self._MERIT_SCORE_ATTRS)
            try:
                merits[str(name)] = float(score) if score is not None else 0.0
            except (TypeError, ValueError):
                merits[str(name)] = 0.0
        return merits

    @staticmethod
    def _first_attr(obj: Any, names: tuple[str, ...]) -> Any:
        """Return the first present, non-``None`` attribute among *names*."""
        for attr in names:
            value = getattr(obj, attr, None)
            if value is not None:
                return value
        return None

    def _minister_domain(self, minister: str) -> str:
        """Return a minister's genome domain, lower-cased; ``""`` if unknown."""
        try:
            genome = self._court._sm._genomes.get(minister)
        except Exception:
            return ""
        if genome is None:
            return ""
        return str(getattr(genome, "domain", "") or "").strip().lower()

    def _classify_request(self, prompt: str, domain: str) -> Optional[Any]:
        """Classify a request into a routing capability.

        Returns ``None`` when no router is attached or the router cannot make
        a meaningful (non-``UNKNOWN``) decision, in which case capability-based
        matching is skipped entirely rather than guessed at.
        """
        if self._smart_router is None:
            return None
        try:
            cap = self._smart_router.classify(prompt or "", domain)
        except Exception:
            logger.error(
                "[TaskEngine] SmartRouter.classify 失败，本次选臣跳过能力匹配",
                exc_info=True,
            )
            return None
        return None if self._is_unknown_capability(cap) else cap

    def _classify_domain(self, minister_domain: str) -> Optional[Any]:
        """Map a minister's domain string onto a routing capability."""
        if self._smart_router is None:
            return None
        try:
            cap = self._smart_router.classify_domain(minister_domain)
        except Exception:
            logger.error(
                "[TaskEngine] SmartRouter.classify_domain('%s') 失败",
                minister_domain, exc_info=True,
            )
            return None
        return None if self._is_unknown_capability(cap) else cap

    @staticmethod
    def _is_unknown_capability(cap: Any) -> bool:
        """Whether a router verdict carries no usable routing information."""
        if cap is None:
            return True
        return str(getattr(cap, "value", cap)).lower() in ("unknown", "")

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
