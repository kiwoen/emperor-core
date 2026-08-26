"""
ConsensusEngine — multi-minister deliberation & consensus formation.

Core workflow:
    1. Select N ministers to participate
    2. Each minister independently processes the task
    3. Collect all outputs (answer + reasoning)
    4. Cross-critique: ministers evaluate each other's outputs
    5. Apply the chosen consensus strategy to produce a final answer

Configurable parameters:
    - num_ministers: how many ministers participate
    - critique_rounds: how many rounds of cross-critique
    - strategy: which voting/synthesis algorithm to use
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from huanxin.consensus.strategies import (
    ConsensusResult,
    ConsensusStrategy,
    CritiqueResult,
    MinisterOutput,
    MajorityVote,
)

logger = logging.getLogger("huanxin.consensus.engine")


# ══════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════


@dataclass
class ConsensusConfig:
    """Configuration for ConsensusEngine."""

    num_ministers: int = 3
    critique_rounds: int = 1
    require_critique: bool = True
    strategy: Optional[ConsensusStrategy] = None  # defaults to MajorityVote


# ══════════════════════════════════════════════════════════════════
# Engine
# ══════════════════════════════════════════════════════════════════


class ConsensusEngine:
    """Orchestrates multi-minister deliberation and consensus formation.

    Usage:
        engine = ConsensusEngine(court, config=ConsensusConfig(num_ministers=5))

        # Define a task executor (how each minister produces output)
        def my_executor(minister_name: str, task: str) -> MinisterOutput:
            return MinisterOutput(
                minister=minister_name,
                answer="Paris",
                reasoning="Capital of France",
                confidence=0.92,
                merit_score=75.0,
            )

        result = engine.deliberate("What is the capital of France?",
                                    executor=my_executor,
                                    strategy=MajorityVote())
    """

    def __init__(
        self,
        court: Any = None,
        config: Optional[ConsensusConfig] = None,
    ) -> None:
        """
        Args:
            court: Optional Court instance for merit-score lookup.
            config: ConsensusConfig with num_ministers, critique_rounds, etc.
        """
        self._court = court
        self.config = config or ConsensusConfig()

        # Default critique evaluator
        self._critique_evaluator: Optional[Callable] = None

    # ── Public API ─────────────────────────────────────────────────

    def deliberate(
        self,
        task: str,
        *,
        ministers: Optional[list[str]] = None,
        executor: Callable[[str, str], MinisterOutput],
        strategy: Optional[ConsensusStrategy] = None,
        critique_evaluator: Optional[Callable] = None,
    ) -> ConsensusResult:
        """Run the full deliberation pipeline.

        Args:
            task: The task/question to deliberate on.
            ministers: Optional list of minister names. If None, selects
                from court if available, or raises ValueError.
            executor: Callable (minister_name, task) -> MinisterOutput.
                Each minister must produce an answer including reasoning.
            strategy: ConsensusStrategy to use for final resolution.
                Defaults to MajorityVote.
            critique_evaluator: Optional (critic_name, target_output) ->
                CritiqueResult. Used for cross-critique. If None and
                critique is required, a simple heuristic evaluator is used.

        Returns:
            ConsensusResult with final_answer and full metadata.
        """
        if ministers is None:
            ministers = self._select_ministers()

        if not ministers:
            raise ValueError(
                "No ministers available for deliberation. "
                "Provide a ministers list or register ministers in the court."
            )

        if len(ministers) < 2:
            raise ValueError(
                f"Need at least 2 ministers for deliberation, got {len(ministers)}"
            )

        # ── Step 1: Independent deliberation ──
        outputs: list[MinisterOutput] = []
        for name in ministers:
            try:
                out = executor(name, task)
                # Enrich with merit score from court if available
                if self._court is not None and out.merit_score == 50.0:
                    out.merit_score = self._get_merit_score(name)
                outputs.append(out)
            except Exception as exc:
                logger.warning(
                    "Minister '%s' failed to deliberate: %s", name, exc,
                )
                outputs.append(MinisterOutput(
                    minister=name,
                    answer="",
                    reasoning=f"ERROR: {exc}",
                    confidence=0.0,
                    merit_score=self._get_merit_score(name),
                ))

        logger.info(
            "[Consensus] %d ministers deliberated on task (len=%d chars)",
            len(outputs), len(task),
        )

        # ── Step 2: Cross-critique ──
        critiques: list[CritiqueResult] = []
        if self.config.require_critique and len(outputs) >= 2:
            actual_evaluator = critique_evaluator or self._critique_evaluator
            critiques = self._run_cross_critique(outputs, actual_evaluator)
            logger.info(
                "[Consensus] %d cross-critiques completed", len(critiques),
            )

        # ── Step 3: Resolve consensus ──
        chosen_strategy = strategy or self.config.strategy or MajorityVote()
        result = chosen_strategy.resolve(
            outputs,
            critiques=critiques or None,
        )
        result.critiques = critiques

        logger.info(
            "[Consensus] Final answer: '%s' (strategy=%s, confidence=%.4f)",
            result.final_answer[:120], result.strategy, result.confidence,
        )
        return result

    # ── Private methods ────────────────────────────────────────────

    def _select_ministers(self) -> list[str]:
        """Select ministers from the court based on config.num_ministers."""
        if self._court is None:
            return []

        active = self._court.active_ministers
        if not active:
            return []

        # Try to get merit-ranked list for best selection
        try:
            ranking = self._court.merit_ranking
            ranked_names = [r.minister for r in ranking if r.minister in active]
        except Exception:
            ranked_names = active

        # Pick top N ministers (or all if fewer)
        selected = ranked_names[:self.config.num_ministers]
        if len(selected) < self.config.num_ministers:
            selected = active[:self.config.num_ministers]

        return selected

    def _get_merit_score(self, minister_name: str) -> float:
        """Look up a minister's merit score from the court."""
        if self._court is None:
            return 50.0

        try:
            ranking = self._court.merit_ranking
            for r in ranking:
                if r.minister == minister_name:
                    return float(r.merit_score)
        except Exception:
            pass

        return 50.0

    def _run_cross_critique(
        self,
        outputs: list[MinisterOutput],
        evaluator: Optional[Callable] = None,
    ) -> list[CritiqueResult]:
        """Each minister critiques every other minister's output.

        Args:
            outputs: List of MinisterOutput from deliberation.
            evaluator: (critic_name, target_output) -> CritiqueResult.

        Returns:
            List of CritiqueResult, one per critic-target pair.
        """
        critiques: list[CritiqueResult] = []

        for critic_out in outputs:
            for target_out in outputs:
                if critic_out.minister == target_out.minister:
                    continue  # skip self-critique

                try:
                    if evaluator is not None:
                        critique = evaluator(critic_out, target_out)
                    else:
                        critique = self._default_critique_evaluator(
                            critic_out, target_out,
                        )
                    critiques.append(critique)
                except Exception as exc:
                    logger.debug(
                        "Critique %s → %s failed: %s",
                        critic_out.minister, target_out.minister, exc,
                    )

        return critiques

    def _default_critique_evaluator(
        self,
        critic: MinisterOutput,
        target: MinisterOutput,
    ) -> CritiqueResult:
        """Simple heuristic critique when no LLM evaluator is provided.

        Scores based on:
        - Answer similarity (rough string overlap)
        - Confidence of target
        - Reasoning length (proxy for thoroughness)
        """
        # Simple similarity: character-level Jaccard-like ratio
        a = set(target.answer)
        b = set(critic.answer)
        if not a and not b:
            similarity = 1.0
        elif not a or not b:
            similarity = 0.0
        else:
            similarity = len(a & b) / max(len(a | b), 1)

        # Score formula: blend of similarity and target confidence
        score = 0.5 * similarity + 0.4 * target.confidence + 0.1 * target.merit_score / 100.0

        strengths = []
        weaknesses = []

        if similarity > 0.7:
            strengths.append("答案与评审者观点高度一致")
        if target.confidence > 0.8:
            strengths.append("置信度较高")
        if len(target.reasoning) > 50:
            strengths.append("推理过程详尽")
        if similarity < 0.3:
            weaknesses.append("答案与评审者观点差异大")
        if target.confidence < 0.5:
            weaknesses.append("置信度偏低")
        if not target.reasoning:
            weaknesses.append("缺乏推理过程")

        return CritiqueResult(
            critic=critic.minister,
            target=target.minister,
            score=min(1.0, max(0.0, score)),
            strengths=strengths,
            weaknesses=weaknesses,
            summary=f"{critic.minister} 评审 {target.minister}: "
            f"相似度={similarity:.2f}, 评分={score:.2f}",
        )

    # ── Convenience / configuration ─────────────────────────────────

    def set_critique_evaluator(self, evaluator: Callable) -> None:
        """Set a custom critique evaluator.

        Args:
            evaluator: (critic: MinisterOutput, target: MinisterOutput) ->
                CritiqueResult.
        """
        self._critique_evaluator = evaluator
