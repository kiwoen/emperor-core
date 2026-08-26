"""RealTaskFitness — fitness from real task outcomes, not response length.

This module implements P0.3 of the self-evolving-AI plan.

The old scorer (``huanxin.court.task_engine._simple_confidence``) awarded up to
``+0.30`` purely for ``len(response) / 2000``.  Because that confidence became
the minister's merit, and merit drove the evolutionary survival mechanism, the
whole "self-evolution" loop was optimising for *verbosity*: a textbook reward
hacking channel.  Nothing in the signal reflected whether the task actually
succeeded.

The replacement signal is:

    fitness = 0.6 * task_success + 0.4 * test_pass_rate

with an optional pluggable evaluator (deepeval / SWE-bench / LLM judge) that
can supply a third, quality-oriented component.  When no test signal exists
the score saturates at the execution weight (0.6) — a task that merely ran is
never worth as much as a task that ran *and* passed its tests.

Usage::

    from huanxin.court.fitness import RealTaskFitness, FitnessSignal

    fitness = RealTaskFitness()
    fitness.score(FitnessSignal(execution_success=True, test_pass_rate=1.0))
    # → 1.0
    fitness.score(FitnessSignal(execution_success=False, response="x" * 5000))
    # → 0.0   (length no longer buys anything)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

logger = logging.getLogger("huanxin.court.fitness")


# ══════════════════════════════════════════════════════════════════
# Signal
# ══════════════════════════════════════════════════════════════════


@dataclass
class FitnessSignal:
    """Everything the fitness function is allowed to look at.

    Attributes:
        execution_success: Did the task actually complete without error?
        test_pass_rate: Fraction of unit tests passed (0.0–1.0), or ``None``
            when the task produced no test signal.
        response: The produced text.  Kept **only** for evaluator plug-ins and
            emptiness checks — it deliberately contributes no length bonus.
        expected: Optional ground-truth answer used for correctness checking.
        error: Error string when execution failed.
        domain: Task domain, forwarded to evaluators.
        meta: Free-form extra context for evaluators.
    """

    execution_success: bool = False
    test_pass_rate: Optional[float] = None
    response: str = ""
    expected: Optional[str] = None
    error: Optional[str] = None
    domain: str = "general"
    meta: Dict[str, Any] = field(default_factory=dict)

    def matches_expected(self) -> Optional[bool]:
        """Return whether *response* satisfies *expected*.

        Returns ``None`` when no ground truth was supplied (nothing to check).
        """
        if self.expected is None or not self.expected.strip():
            return None
        return self.expected.strip().lower() in (self.response or "").strip().lower()


# ══════════════════════════════════════════════════════════════════
# Evaluator plug-in point
# ══════════════════════════════════════════════════════════════════


@runtime_checkable
class QualityEvaluator(Protocol):
    """Pluggable quality evaluator (deepeval, SWE-bench, LLM judge, …).

    Implementations return a normalised 0.0–1.0 quality score, or ``None``
    when they cannot judge the sample.
    """

    def evaluate(self, signal: FitnessSignal) -> Optional[float]:  # pragma: no cover
        ...


class NullEvaluator:
    """Default evaluator — abstains on every sample.

    Kept as a concrete class (rather than ``None``) so the wiring for a real
    evaluator is already exercised by the default code path.
    """

    def evaluate(self, signal: FitnessSignal) -> Optional[float]:
        """Always abstain."""
        return None


# ══════════════════════════════════════════════════════════════════
# RealTaskFitness
# ══════════════════════════════════════════════════════════════════


class RealTaskFitness:
    """Fitness = real task success (0.6) + unit-test pass rate (0.4).

    Args:
        execution_weight: Weight of the "task actually succeeded" component.
        test_weight: Weight of the unit-test pass-rate component.
        evaluator: Optional :class:`QualityEvaluator`.  When it returns a
            score, that score is blended in with ``evaluator_weight``.
        evaluator_weight: Blend factor for the evaluator score (0.0–1.0).

    The two primary weights are normalised so they always sum to 1.0, which
    keeps the output in ``[0.0, 1.0]`` for any caller-supplied weighting.
    """

    DEFAULT_EXECUTION_WEIGHT: float = 0.6
    DEFAULT_TEST_WEIGHT: float = 0.4

    def __init__(
        self,
        execution_weight: float = DEFAULT_EXECUTION_WEIGHT,
        test_weight: float = DEFAULT_TEST_WEIGHT,
        evaluator: Optional[QualityEvaluator] = None,
        evaluator_weight: float = 0.0,
    ) -> None:
        total = float(execution_weight) + float(test_weight)
        if total <= 0.0:
            raise ValueError(
                "execution_weight + test_weight must be > 0 "
                f"(got {execution_weight} + {test_weight})"
            )
        self.execution_weight: float = float(execution_weight) / total
        self.test_weight: float = float(test_weight) / total
        self.evaluator: QualityEvaluator = evaluator or NullEvaluator()
        self.evaluator_weight: float = max(0.0, min(1.0, float(evaluator_weight)))

    # ── Core scoring ──────────────────────────────────────────────

    def score(self, signal: FitnessSignal) -> float:
        """Compute fitness in ``[0.0, 1.0]`` for a single task signal.

        Rules:
            * Execution failure → ``0.0``.  No partial credit, no length bonus.
            * Ground truth supplied but not matched → ``0.0``.  A confidently
              wrong answer is a failed task.
            * Empty response → ``0.0``.
            * Otherwise ``execution_weight`` plus ``test_weight * pass_rate``.
            * An evaluator score, when available, is blended in.
        """
        if not signal.execution_success:
            return 0.0
        if not (signal.response or "").strip():
            return 0.0
        if signal.matches_expected() is False:
            return 0.0

        base = self.execution_weight

        pass_rate = self._normalise_rate(signal.test_pass_rate)
        if pass_rate is not None:
            base += self.test_weight * pass_rate

        eval_score = self._safe_evaluate(signal)
        if eval_score is not None and self.evaluator_weight > 0.0:
            base = (
                base * (1.0 - self.evaluator_weight)
                + eval_score * self.evaluator_weight
            )

        return round(max(0.0, min(1.0, base)), 4)

    def score_outcome(
        self,
        outcome: Any,
        test_pass_rate: Optional[float] = None,
    ) -> float:
        """Score an existing ``TaskOutcome``-like object.

        Accepts any object exposing ``success``, ``raw_response`` and
        ``error`` attributes, so it works with ``TaskOutcome`` without
        importing it (avoiding a circular import with ``task_engine``).
        """
        return self.score(
            FitnessSignal(
                execution_success=bool(getattr(outcome, "success", False)),
                test_pass_rate=test_pass_rate,
                response=str(getattr(outcome, "raw_response", "") or ""),
                error=getattr(outcome, "error", None),
            )
        )

    def __call__(self, *args: Any, **kwargs: Any) -> float:
        """Flexible entry point so the object can act as a drop-in scorer.

        Supported call shapes:
            * ``fitness(signal)`` — a :class:`FitnessSignal`.
            * ``fitness(outcome, test_pass_rate=...)`` — a TaskOutcome-like.
            * ``fitness(response, expected)`` — legacy ``_simple_confidence``
              signature.  Execution success is inferred from a non-empty
              response, so legacy callers keep working (and now get a
              length-independent score).
        """
        if "signal" in kwargs:
            return self.score(kwargs["signal"])

        if args and isinstance(args[0], FitnessSignal):
            return self.score(args[0])

        if args and hasattr(args[0], "raw_response"):
            return self.score_outcome(args[0], kwargs.get("test_pass_rate"))

        response = str(args[0]) if args else str(kwargs.get("response", ""))
        expected = args[1] if len(args) > 1 else kwargs.get("expected")
        return self.score(
            FitnessSignal(
                execution_success=bool(response.strip()),
                test_pass_rate=kwargs.get("test_pass_rate"),
                response=response,
                expected=expected,
            )
        )

    # ── Internals ─────────────────────────────────────────────────

    @staticmethod
    def _normalise_rate(rate: Optional[float]) -> Optional[float]:
        """Clamp a pass rate into ``[0.0, 1.0]``; ``None`` stays ``None``."""
        if rate is None:
            return None
        try:
            return max(0.0, min(1.0, float(rate)))
        except (TypeError, ValueError):
            logger.warning("[RealTaskFitness] non-numeric test_pass_rate=%r", rate)
            return None

    def _safe_evaluate(self, signal: FitnessSignal) -> Optional[float]:
        """Run the evaluator, converting any failure into an explicit abstain."""
        try:
            raw = self.evaluator.evaluate(signal)
        except Exception:
            logger.warning(
                "[RealTaskFitness] evaluator %s raised — abstaining",
                type(self.evaluator).__name__,
                exc_info=True,
            )
            return None
        if raw is None:
            return None
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            logger.warning("[RealTaskFitness] evaluator returned non-numeric %r", raw)
            return None

    def describe(self) -> Dict[str, Any]:
        """Return a serialisable description of the active weighting."""
        return {
            "execution_weight": round(self.execution_weight, 4),
            "test_weight": round(self.test_weight, 4),
            "evaluator": type(self.evaluator).__name__,
            "evaluator_weight": round(self.evaluator_weight, 4),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"RealTaskFitness(execution={self.execution_weight:.2f}, "
            f"test={self.test_weight:.2f}, "
            f"evaluator={type(self.evaluator).__name__})"
        )


# ══════════════════════════════════════════════════════════════════
# EvalBenchEvaluator — P0.6 真实正确性信号（opt-in）
# ══════════════════════════════════════════════════════════════════


class EvalBenchEvaluator:
    """接入 P0.6 确定性评测基准的可选质量评估器（实现 :class:`QualityEvaluator`）。

    当任务提供了 ground-truth ``expected`` 时，用
    :class:`~huanxin.eval_bench.judges.DeterministicJudge` 判定产出的
    ``response`` 是否正确，返回 1.0 / 0.0。未提供 ground truth 时**弃权**
    （返回 ``None``），从而保持 P0.3 的"成败 + 单测通过率"为主。

    是否启用真实信号由 ``HUANXIN_JUDGE_MODE`` 门控：仅 ``"deterministic"`` /
    ``"llm"`` 提供真实正确性信号；``"heuristic"`` 弃权，避免把假高分喂进适应度。

    用法（不改变默认行为）：:

        from huanxin.court.fitness import make_eval_bench_fitness
        fitness = make_eval_bench_fitness(evaluator_weight=0.2)
        # 默认 RealTaskFitness() 行为完全不变（NullEvaluator, weight=0）
    """

    def evaluate(self, signal: "FitnessSignal") -> Optional[float]:
        if not signal.expected or not signal.expected.strip():
            return None
        from huanxin.eval_bench.criteria import EvalCase as _BenchCase
        from huanxin.eval_bench.judges import (
            DeterministicJudge as _BenchDet,
            default_correctness as _bench_default,
            resolve_judge_mode,
        )

        mode = resolve_judge_mode()
        if mode == "heuristic":
            # 绝不把启发式假高分喂进适应度。
            return None

        case = _BenchCase(
            input="",
            expected=signal.expected or "",
            gold_validator=_bench_default,
            domain=signal.domain or "general",
        )
        res = _BenchDet().judge(case, signal.response or "")
        return res.score


def make_eval_bench_fitness(evaluator_weight: float = 0.2) -> "RealTaskFitness":
    """构造一个把 P0.6 评测基准并入适应度的 :class:`RealTaskFitness`。

    基准分量权重默认很小（0.2），使 P0.3 信号（任务成败 + 单测通过率）保持主导。
    传入 ``evaluator_weight=0.0`` 即退化为纯 P0.3 行为。

    Args:
        evaluator_weight: 评测基准分量在适应度中的混合权重（0.0–1.0）。
    """
    return RealTaskFitness(
        evaluator=EvalBenchEvaluator(),
        evaluator_weight=evaluator_weight,
    )


__all__ = [
    "FitnessSignal",
    "QualityEvaluator",
    "NullEvaluator",
    "RealTaskFitness",
    "EvalBenchEvaluator",
    "make_eval_bench_fitness",
]
