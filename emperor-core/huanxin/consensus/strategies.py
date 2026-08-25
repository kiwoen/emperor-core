"""
Consensus Strategies — voting & synthesis algorithms for multi-agent deliberation.

Each strategy implements a different approach to producing a final answer
from multiple ministers' independently produced outputs.

Strategies:
    MajorityVote      — simple majority: pick the most common answer
    WeightedVote      — weight votes by minister merit/quality
    DebateRound       — multi-round debate with revision
    BestOfN           — select the highest-confidence single answer
    SynthesisConsensus — LLM-powered synthesis of all opinions
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("huanxin.consensus.strategies")


# ══════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════


@dataclass
class MinisterOutput:
    """Output from a single minister's deliberation."""

    minister: str
    answer: str
    reasoning: str = ""
    confidence: float = 0.75
    merit_score: float = 50.0


@dataclass
class CritiqueResult:
    """Result of one minister critiquing another's output."""

    critic: str
    target: str
    score: float  # 0.0-1.0 quality rating
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class ConsensusResult:
    """Final consensus output from the engine."""

    final_answer: str
    strategy: str
    num_ministers: int
    num_rounds: int
    confidence: float
    votes: dict[str, int] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    critiques: list[CritiqueResult] = field(default_factory=list)
    debate_log: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════
# Base class
# ══════════════════════════════════════════════════════════════════


class ConsensusStrategy(ABC):
    """Abstract base for all consensus strategies."""

    name: str = "base"

    @abstractmethod
    def resolve(
        self,
        outputs: list[MinisterOutput],
        critiques: Optional[list[CritiqueResult]] = None,
        debate_log: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ConsensusResult:
        """Produce a final consensus from minister outputs.

        Args:
            outputs: MinisterOutput list from all participating ministers.
            critiques: Optional cross-critique results.
            debate_log: Optional log of debate rounds.
            **kwargs: Strategy-specific parameters.

        Returns:
            ConsensusResult with final_answer and metadata.
        """
        ...


# ══════════════════════════════════════════════════════════════════
# Strategy implementations
# ══════════════════════════════════════════════════════════════════


class MajorityVote(ConsensusStrategy):
    """Simple majority vote — the answer with the most votes wins.

    If there is a tie, the answer with the highest average confidence wins.
    """

    name = "majority_vote"

    def resolve(
        self,
        outputs: list[MinisterOutput],
        critiques: Optional[list[CritiqueResult]] = None,
        debate_log: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ConsensusResult:
        if not outputs:
            return ConsensusResult(
                final_answer="",
                strategy=self.name,
                num_ministers=0,
                num_rounds=1,
                confidence=0.0,
            )

        answers = [o.answer.strip() for o in outputs]
        counter = Counter(answers)
        votes = dict(counter)

        # Find the most common answer; break ties by avg confidence
        max_count = max(counter.values())
        top_answers = [a for a, c in counter.items() if c == max_count]

        if len(top_answers) == 1:
            winner = top_answers[0]
        else:
            # Tie-break: highest average confidence
            conf_sums: dict[str, tuple[float, int]] = {}
            for ans in top_answers:
                confs = [o.confidence for o in outputs if o.answer.strip() == ans]
                conf_sums[ans] = (sum(confs), len(confs))
            winner = max(top_answers, key=lambda a: conf_sums[a][0] / conf_sums[a][1])

        scores = {a: c / len(outputs) for a, c in counter.items()}
        avg_conf = sum(o.confidence for o in outputs) / len(outputs) if outputs else 0.0

        return ConsensusResult(
            final_answer=winner,
            strategy=self.name,
            num_ministers=len(outputs),
            num_rounds=1,
            confidence=avg_conf * (max_count / len(outputs)),
            votes=votes,
            scores=scores,
        )


class WeightedVote(ConsensusStrategy):
    """Weighted vote based on minister merit scores.

    Each minister's vote counts proportionally to their merit score.
    Weights can be blended with confidence for stability.
    """

    name = "weighted_vote"

    def __init__(self, merit_weight: float = 0.6, confidence_weight: float = 0.4):
        """
        Args:
            merit_weight: Weight for merit_score (0-1).
            confidence_weight: Weight for per-task confidence (0-1).
                Must sum to 1.0 with merit_weight.
        """
        self.merit_weight = merit_weight
        self.confidence_weight = confidence_weight

    def resolve(
        self,
        outputs: list[MinisterOutput],
        critiques: Optional[list[CritiqueResult]] = None,
        debate_log: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ConsensusResult:
        # Compute composite weight for each minister
        weights: dict[str, float] = {}
        for o in outputs:
            merit_norm = o.merit_score / 100.0
            composite = (
                self.merit_weight * merit_norm
                + self.confidence_weight * o.confidence
            )
            weights[o.minister] = composite

        # Accumulate weighted votes per answer
        answer_scores: dict[str, float] = {}
        for o in outputs:
            key = o.answer.strip()
            answer_scores[key] = answer_scores.get(key, 0.0) + weights[o.minister]

        if not answer_scores:
            return ConsensusResult(
                final_answer="",
                strategy=self.name,
                num_ministers=len(outputs),
                num_rounds=1,
                confidence=0.0,
            )

        winner = max(answer_scores, key=answer_scores.get)
        total_weight = sum(weights.values())
        scores = {k: v / total_weight for k, v in answer_scores.items()} if total_weight > 0 else {}

        return ConsensusResult(
            final_answer=winner,
            strategy=self.name,
            num_ministers=len(outputs),
            num_rounds=1,
            confidence=answer_scores[winner] / total_weight if total_weight > 0 else 0,
            scores=scores,
            metadata={"weights": weights},
        )


class DebateRound(ConsensusStrategy):
    """Multi-round debate where ministers can revise their answers.

    Each round:
        1. Ministers see others' answers and critiques
        2. Each can revise based on peer feedback
        3. Repeat for N rounds
        4. Final vote on last-round answers

    The revision logic is pluggable via a callback for LLM integration.
    """

    name = "debate_round"

    def __init__(
        self,
        rounds: int = 2,
        revision_callback: Optional[callable] = None,
    ):
        """
        Args:
            rounds: Number of debate rounds.
            revision_callback: Optional (minister_name, own_answer, all_outputs,
                critiques) -> new_answer. If None, ministers don't revise.
        """
        self.rounds = rounds
        self.revision_callback = revision_callback

    def resolve(
        self,
        outputs: list[MinisterOutput],
        critiques: Optional[list[CritiqueResult]] = None,
        debate_log: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ConsensusResult:
        log: list[dict[str, Any]] = list(debate_log) if debate_log else []
        current_outputs = outputs
        current_critiques = critiques or []

        for round_num in range(1, self.rounds + 1):
            # Log the round
            round_entry: dict[str, Any] = {
                "round": round_num,
                "answers": {
                    o.minister: {"answer": o.answer, "confidence": o.confidence}
                    for o in current_outputs
                },
            }
            log.append(round_entry)

            # If revision callback is provided, ministers can revise
            if self.revision_callback and round_num < self.rounds:
                revised: list[MinisterOutput] = []
                for o in current_outputs:
                    rev_answer = self.revision_callback(
                        o.minister, o.answer, current_outputs, current_critiques,
                    )
                    if rev_answer and rev_answer != o.answer:
                        revised.append(MinisterOutput(
                            minister=o.minister,
                            answer=rev_answer,
                            reasoning=f"[Round {round_num+1} revision] " + o.reasoning,
                            confidence=o.confidence * 0.95,  # slight confidence decay
                            merit_score=o.merit_score,
                        ))
                        round_entry.setdefault("revisions", {})[o.minister] = rev_answer
                    else:
                        revised.append(o)
                current_outputs = revised

        # Final vote: majority on last-round answers
        final = MajorityVote().resolve(current_outputs, current_critiques)
        final.strategy = self.name
        final.num_rounds = self.rounds
        final.debate_log = log
        return final


class BestOfN(ConsensusStrategy):
    """Select the single answer with the highest confidence.

    If multiple answers have the same confidence, use merit_score as
    tiebreaker. If the top answer's confidence is below a threshold,
    optionally fall back to majority vote.
    """

    name = "best_of_n"

    def __init__(self, fallback_threshold: float = 0.0):
        """
        Args:
            fallback_threshold: If best confidence < threshold, fall back
                to majority vote. Default 0.0 disables fallback.
        """
        self.fallback_threshold = fallback_threshold

    def resolve(
        self,
        outputs: list[MinisterOutput],
        critiques: Optional[list[CritiqueResult]] = None,
        debate_log: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ConsensusResult:
        if not outputs:
            return ConsensusResult(
                final_answer="",
                strategy=self.name,
                num_ministers=0,
                num_rounds=1,
                confidence=0.0,
            )

        # Sort by confidence desc, then merit_score desc
        sorted_outputs = sorted(
            outputs,
            key=lambda o: (o.confidence, o.merit_score),
            reverse=True,
        )
        best = sorted_outputs[0]

        scores = {o.answer.strip(): o.confidence for o in outputs}

        # Fallback check
        if self.fallback_threshold > 0 and best.confidence < self.fallback_threshold:
            logger.info(
                "BestOfN confidence %.4f below threshold %.4f, falling back to majority",
                best.confidence, self.fallback_threshold,
            )
            fallback = MajorityVote().resolve(outputs)
            fallback.strategy = f"{self.name}[fallback_to_majority]"
            return fallback

        return ConsensusResult(
            final_answer=best.answer,
            strategy=self.name,
            num_ministers=len(outputs),
            num_rounds=1,
            confidence=best.confidence,
            scores=scores,
            metadata={"selected_minister": best.minister},
        )


class SynthesisConsensus(ConsensusStrategy):
    """LLM-powered synthesis of all minister opinions into a final answer.

    Instead of voting, feed all minister outputs and cross-critiques into
    an LLM that synthesizes a comprehensive final answer. This is ideal
    when answers are nuanced and not simple A/B/C choices.

    The synthesis prompt is pluggable; by default it concatenates all
    opinions and asks for a synthesized answer.
    """

    name = "synthesis_consensus"

    def __init__(self, llm_callback: Optional[callable] = None):
        """
        Args:
            llm_callback: Callable(prompt: str) -> str. If None,
                the strategy produces a naive synthesis (concatenation)
                for testing purposes.
        """
        self.llm_callback = llm_callback

    def resolve(
        self,
        outputs: list[MinisterOutput],
        critiques: Optional[list[CritiqueResult]] = None,
        debate_log: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ConsensusResult:
        if not outputs:
            return ConsensusResult(
                final_answer="",
                strategy=self.name,
                num_ministers=0,
                num_rounds=1,
                confidence=0.0,
            )

        # Build synthesis prompt
        opinions = "\n\n".join(
            f"[{o.minister} (置信度={o.confidence:.2f}, 含金量={o.merit_score:.0f})]\n"
            f"答案: {o.answer}\n理由: {o.reasoning}"
            for o in outputs
        )

        if critiques:
            critique_text = "\n\n".join(
                f"[{c.critic} 评审 {c.target}] 评分={c.score:.2f}\n"
                f"优点: {'; '.join(c.strengths)}\n缺点: {'; '.join(c.weaknesses)}\n"
                f"总结: {c.summary}"
                for c in critiques
            )
        else:
            critique_text = "无交叉评审"

        prompt = (
            f"以下{len(outputs)}个大臣就同一任务各自给出了答案。请综合所有意见，"
            f"给出一个最终的综合答案。\n\n"
            f"=== 大臣意见 ===\n{opinions}\n\n"
            f"=== 交叉评审 ===\n{critique_text}\n\n"
            f"请给出综合后的最终答案，要求：\n"
            f"1. 融合各家的优点\n2. 标注使用了哪些大臣的观点\n"
            f"3. 给出综合置信度(0-1)"
        )

        if self.llm_callback:
            final_answer = self.llm_callback(prompt)
        else:
            # Naive fallback: concatenate unique answers
            unique_answers = list(dict.fromkeys(o.answer for o in outputs))
            final_answer = (
                f"[合成结论 - 基于{len(outputs)}位大臣意见]\n\n"
                + "\n\n---\n\n".join(
                    f"来自 {o.minister}: {o.answer}" for o in outputs
                )
                + f"\n\n唯一意见数: {len(unique_answers)}"
            )

        avg_conf = sum(o.confidence for o in outputs) / len(outputs)

        return ConsensusResult(
            final_answer=final_answer,
            strategy=self.name,
            num_ministers=len(outputs),
            num_rounds=1,
            confidence=avg_conf,
            scores={o.minister: o.confidence for o in outputs},
            metadata={"llm_used": self.llm_callback is not None},
        )
