"""
ReflectionConsensus (三省合议) — multi-pass debate synthesis for the Imperial Court.

Inspired by:
    - Tang Dynasty 三省六部: 中书省 drafts → 门下省 reviews → 尚书省 executes
    - Roman Senate: structured debate → voting → decree
    - Modern consensus algorithms: Raft-like leader election among ministers

The ReflectionConsensus replaces the naive "concatenate and ship" synthesis
with a structured multi-round process:

    Phase 1: Cross-Critique — ministers review each other's outputs
    Phase 2: Weighted Voting — ministers vote on key decisions, weighted by merit
    Phase 3: Draft Synthesis — best output is selected as base, supplemented
    Phase 4: Self-Reflection — draft is critiqued and revised
    Phase 5: Final Decree — polished output with quality score

Key principle: the best ideas survive through competition and cross-review,
not through simple aggregation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger("jarvis.court.reflection")


class ConsensusPhase(Enum):
    """Stages of the reflection consensus pipeline."""
    CROSS_CRITIQUE = auto()     # Ministers critique each other
    WEIGHTED_VOTE = auto()      # Vote on key decisions
    DRAFT_SYNTHESIS = auto()    # Build initial draft
    SELF_REFLECTION = auto()    # Critique and revise draft
    FINAL_DECREE = auto()       # Polished output


class CritiqueDirection(Enum):
    """Direction of a critique."""
    SUPPORT = auto()       # Agree and amplify
    CHALLENGE = auto()     # Disagree and counter
    REFINE = auto()        # Agree but suggest improvements
    NEUTRAL = auto()       # No strong opinion


@dataclass
class Critique:
    """A single critique from one minister to another."""
    from_minister: str
    to_minister: str
    direction: CritiqueDirection
    points: list[str]             # Specific critique points
    confidence: float             # How confident is the critique
    synthesis_value: float = 0.5  # How valuable this critique is for synthesis


@dataclass
class Vote:
    """A weighted vote on a key decision."""
    voter: str
    option: str                   # Which option they vote for
    weight: float                 # Merit-based weight
    reasoning: str
    confidence: float


@dataclass
class SynthesisDraft:
    """Intermediate draft during synthesis."""
    version: int
    content: str
    base_minister: str             # Whose output serves as base
    incorporated_from: list[str]   # Other ministers whose ideas were merged
    critiques_applied: int          # How many critiques were addressed
    quality_score: float
    issues: list[str] = field(default_factory=list)


@dataclass
class ConsensusResult:
    """Final result of the reflection consensus process."""
    decree_content: str
    quality_score: float
    confidence: float
    phases_completed: list[ConsensusPhase]
    winning_minister: str          # Whose core output was adopted
    contributors: list[str]        # All ministers who contributed
    rounds_of_reflection: int      # How many self-reflection rounds
    dissenting_opinions: list[str]  # Unresolved disagreements
    synthesis_trace: list[str]     # Human-readable trace of synthesis


class ReflectionConsensus:
    """Multi-pass debate synthesis engine.

    Usage:
        consensus = ReflectionConsensus(merit_board=merit_board)

        result = await consensus.synthesize(
            intent="分析代码安全漏洞",
            memorials=memorials,      # List of minister outputs
            censorate_reviews=reviews, # Censorate quality assessments
        )
        # result.decree_content is the final synthesized decree
    """

    MAX_REFLECTION_ROUNDS = 2      # Max self-critique rounds
    MIN_QUALITY_TO_PUBLISH = 0.4   # Below this, decree is rejected

    def __init__(
        self,
        merit_board: Any = None,
        censorate: Any = None,
    ) -> None:
        self._merit_board = merit_board
        self._censorate = censorate

    # ------------------------------------------------------------------
    # Main synthesis pipeline
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        intent: str,
        memorials: list[Any],           # Memorial objects from ministers
        censorate_reviews: Optional[list[Any]] = None,
    ) -> ConsensusResult:
        """Run the full reflection consensus pipeline.

        Args:
            intent: Original user intent
            memorials: List of Memorial objects from minister deliberation
            censorate_reviews: Optional CensorReview objects

        Returns:
            ConsensusResult with final decree and quality metrics
        """
        phases: list[ConsensusPhase] = []
        trace: list[str] = []

        # ── Phase 1: Cross-Critique ─────────────────────────────────
        critiques = self._cross_critique(intent, memorials)
        if critiques:
            phases.append(ConsensusPhase.CROSS_CRITIQUE)
            trace.append(
                f"交叉审查：{len(critiques)} 条意见，"
                f"其中质疑 {sum(1 for c in critiques if c.direction == CritiqueDirection.CHALLENGE)} 条"
            )

        # ── Phase 2: Weighted Voting ────────────────────────────────
        votes = self._weighted_vote(intent, memorials, critiques)
        if votes:
            phases.append(ConsensusPhase.WEIGHTED_VOTE)
            # Find winning option
            option_counts: dict[str, float] = {}
            for v in votes:
                option_counts[v.option] = option_counts.get(v.option, 0.0) + v.weight
            if option_counts:
                winner = max(option_counts, key=option_counts.get)
                trace.append(
                    f"加权投票：{len(votes)} 票，"
                    f"胜出方案 '{winner}' (得分 {option_counts[winner]:.2f})"
                )

        # ── Phase 3: Draft Synthesis ────────────────────────────────
        draft = self._draft_synthesis(intent, memorials, critiques, votes)
        phases.append(ConsensusPhase.DRAFT_SYNTHESIS)
        trace.append(
            f"初稿合成：以 {draft.base_minister} 为基础，"
            f"融合 {len(draft.incorporated_from)} 位大臣意见"
        )

        # ── Phase 4: Self-Reflection ────────────────────────────────
        reflection_rounds = 0
        current_draft = draft
        for round_idx in range(self.MAX_REFLECTION_ROUNDS):
            prev_score = current_draft.quality_score
            new_draft = self._self_reflect(intent, current_draft, memorials, critiques)

            if new_draft.quality_score <= prev_score + 0.05:
                # Reflection didn't significantly improve — stop
                trace.append(
                    f"自反思第{round_idx+1}轮：质量无显著提升 "
                    f"({prev_score:.2f}→{new_draft.quality_score:.2f})，停止"
                )
                break

            current_draft = new_draft
            reflection_rounds += 1
            trace.append(
                f"自反思第{round_idx+1}轮：质量提升 "
                f"({prev_score:.2f}→{new_draft.quality_score:.2f})"
            )

        if reflection_rounds > 0:
            phases.append(ConsensusPhase.SELF_REFLECTION)

        # ── Phase 5: Final Decree ───────────────────────────────────
        phases.append(ConsensusPhase.FINAL_DECREE)
        final_confidence = self._compute_final_confidence(memorials, current_draft)

        # Collect dissenting opinions
        dissenting = self._collect_dissenting(critiques, votes, current_draft)

        # Determine winning minister
        winning = self._determine_winner(memorials, votes, current_draft)

        trace.append(
            f"最终圣旨：品质 {current_draft.quality_score:.2f}，"
            f"置信度 {final_confidence:.2f}，"
            f"主导大臣 {winning}"
        )

        return ConsensusResult(
            decree_content=current_draft.content,
            quality_score=current_draft.quality_score,
            confidence=final_confidence,
            phases_completed=phases,
            winning_minister=winning,
            contributors=[m.minister for m in memorials if m.success],
            rounds_of_reflection=reflection_rounds,
            dissenting_opinions=dissenting,
            synthesis_trace=trace,
        )

    # ------------------------------------------------------------------
    # Phase 1: Cross-Critique
    # ------------------------------------------------------------------

    def _cross_critique(
        self,
        intent: str,
        memorials: list[Any],
    ) -> list[Critique]:
        """Ministers review each other's outputs.

        Each minister critiques the output of every other minister.
        Produces a full N*(N-1) critique matrix.

        In production, this uses real LLMs for each critique pair.
        For now, uses heuristic analysis based on output comparison.
        """
        critiques: list[Critique] = []
        successes = [m for m in memorials if m.success]
        if len(successes) < 2:
            return critiques

        for reviewer in successes:
            for target in successes:
                if reviewer.minister == target.minister:
                    continue

                critique = self._compare_outputs(
                    reviewer, target, intent,
                )
                if critique is not None:
                    critiques.append(critique)

        return critiques

    def _compare_outputs(
        self,
        reviewer: Any,
        target: Any,
        intent: str,
    ) -> Optional[Critique]:
        """Compare two ministers' outputs and generate a critique.

        Heuristic rules for critique direction:
        - If both have similar length and confidence → SUPPORT
        - If reviewer has much higher confidence → CHALLENGE
        - If reviewer has slightly higher confidence → REFINE
        - Otherwise → NEUTRAL
        """
        conf_diff = reviewer.confidence - target.confidence
        len_reviewer = len(reviewer.output)
        len_target = len(target.output)
        len_ratio = len_reviewer / max(1, len_target)

        points: list[str] = []
        direction: CritiqueDirection

        if conf_diff > 0.3:
            direction = CritiqueDirection.CHALLENGE
            points.append(
                f"{reviewer.minister} 认为 {target.minister} 的置信度"
                f"({target.confidence:.0%})不足以支撑其结论"
            )
            if len_ratio > 2.0:
                points.append(
                    f"{reviewer.minister} 提供了更详细的分析"
                    f"({len_reviewer} vs {len_target} 字)"
                )
        elif conf_diff > 0.1:
            direction = CritiqueDirection.REFINE
            points.append(
                f"{reviewer.minister} 建议 {target.minister} 在以下方面深化"
            )
        elif abs(conf_diff) <= 0.1:
            direction = CritiqueDirection.SUPPORT
            points.append(
                f"{reviewer.minister} 认同 {target.minister} 的核心判断"
            )
        else:
            direction = CritiqueDirection.NEUTRAL

        # Check for content overlap (shared key phrases)
        reviewer_words = set(reviewer.output.lower().split())
        target_words = set(target.output.lower().split())
        if reviewer_words and target_words:
            overlap = len(reviewer_words & target_words) / max(
                1, min(len(reviewer_words), len(target_words))
            )
            if overlap < 0.1 and direction == CritiqueDirection.SUPPORT:
                # Supporting but outputs have almost nothing in common
                direction = CritiqueDirection.REFINE
                points.append("两方虽共识但角度差异较大，建议融合")

        # Merit-based synthesis value
        merit_weight = 1.0
        if self._merit_board is not None:
            reviewer_merit = self._merit_board.compute_merit(reviewer.minister)
            target_merit = self._merit_board.compute_merit(target.minister)
            # Higher merit reviewer has more weight
            merit_weight = reviewer_merit / max(1.0, target_merit)

        return Critique(
            from_minister=reviewer.minister,
            to_minister=target.minister,
            direction=direction,
            points=points,
            confidence=reviewer.confidence,
            synthesis_value=min(1.0, merit_weight * reviewer.confidence),
        )

    # ------------------------------------------------------------------
    # Phase 2: Weighted Voting
    # ------------------------------------------------------------------

    def _weighted_vote(
        self,
        intent: str,
        memorials: list[Any],
        critiques: list[Critique],
    ) -> list[Vote]:
        """Conduct weighted voting on the best approach.

        Each minister votes for whose output should form the base
        of the synthesis. Votes are weighted by:
        - Minister's historical merit rank
        - Minister's confidence on this task
        - Censorate quality score (if available)
        """
        votes: list[Vote] = []
        successes = [m for m in memorials if m.success]
        if len(successes) < 2:
            return votes

        # Determine candidates (all successful ministers)
        candidates = [m.minister for m in successes]

        for minister in successes:
            # Compute vote weight
            base_weight = 1.0
            if self._merit_board is not None:
                merit = self._merit_board.compute_merit(minister.minister)
                base_weight = max(0.1, merit / 100.0)

            conf_weight = minister.confidence
            weight = base_weight * 0.6 + conf_weight * 0.4

            # Who gets the vote?
            # Vote for the minister with highest combined merit+confidence
            # that this minister's critiques SUPPORT
            best_candidate = minister.minister  # default: self
            best_score = 0.0

            for candidate in candidates:
                # Find critiques from this minister about candidate
                relevant_critiques = [
                    c for c in critiques
                    if c.from_minister == minister.minister
                    and c.to_minister == candidate
                ]
                support_score = 0.0
                for c in relevant_critiques:
                    if c.direction == CritiqueDirection.SUPPORT:
                        support_score += c.synthesis_value * 2
                    elif c.direction == CritiqueDirection.REFINE:
                        support_score += c.synthesis_value
                    elif c.direction == CritiqueDirection.CHALLENGE:
                        support_score -= c.synthesis_value

                if support_score > best_score:
                    best_score = support_score
                    best_candidate = candidate

            # If no strong preference, vote for self
            if best_score <= 0 and best_candidate == minister.minister:
                best_candidate = max(
                    candidates,
                    key=lambda c: (
                        self._merit_board.compute_merit(c) if self._merit_board else 0
                    ),
                )

            reasoning = f"{minister.minister} 投票给 {best_candidate}"
            if best_candidate == minister.minister:
                reasoning += "（自荐）"

            votes.append(Vote(
                voter=minister.minister,
                option=best_candidate,
                weight=weight,
                reasoning=reasoning,
                confidence=minister.confidence,
            ))

        return votes

    # ------------------------------------------------------------------
    # Phase 3: Draft Synthesis
    # ------------------------------------------------------------------

    def _draft_synthesis(
        self,
        intent: str,
        memorials: list[Any],
        critiques: list[Critique],
        votes: list[Vote],
    ) -> SynthesisDraft:
        """Synthesize a draft decree from the best outputs.

        Strategy:
        1. Pick the highest-voted minister's output as the base
        2. Augment with unique insights from other ministers
        3. Apply agreed-upon improvements from critiques
        4. Note dissenting opinions for the final decree
        """
        successes = [m for m in memorials if m.success]
        if not successes:
            return SynthesisDraft(
                version=1,
                content="诸臣皆默，无策可陈。",
                base_minister="none",
                incorporated_from=[],
                critiques_applied=0,
                quality_score=0.0,
            )

        # Single minister — just use their output
        if len(successes) == 1:
            m = successes[0]
            return SynthesisDraft(
                version=1,
                content=m.output,
                base_minister=m.minister,
                incorporated_from=[],
                critiques_applied=0,
                quality_score=m.confidence,
            )

        # Find the winner of the vote
        winner = self._elect_winner(votes, successes)

        # Determine base content
        base_m = next((m for m in successes if m.minister == winner), successes[0])

        # Build synthesis
        parts: list[str] = []

        # 1. Opening — the selected minister's core insight
        parts.append(base_m.output)

        # 2. Augmentations — unique contributions from other ministers
        augmented: list[str] = []
        for m in successes:
            if m.minister == winner:
                continue

            # Check if this minister had SUPPORT critiques of the winner
            supporting = [
                c for c in critiques
                if c.from_minister == m.minister
                and c.to_minister == winner
                and c.direction == CritiqueDirection.SUPPORT
            ]
            # Check for unique content (low overlap with base)
            base_words = set(base_m.output.lower().split())
            other_words = set(m.output.lower().split())
            if base_words and other_words:
                overlap = len(base_words & other_words) / max(1, min(len(base_words), len(other_words)))
            else:
                overlap = 1.0

            if overlap < 0.5 or supporting:
                augmented.append(m.minister)
                # Add a section header and their unique insight
                unique_start = self._extract_unique_portion(m.output, base_m.output)
                if unique_start:
                    parts.append(f"\n--- {m.minister} 补充见解 ---\n{unique_start}")

        # 3. Dissenting voices — flag disagreements
        dissenting_ministers: list[str] = []
        for c in critiques:
            if c.direction == CritiqueDirection.CHALLENGE:
                if c.to_minister == winner and c.from_minister not in dissenting_ministers:
                    dissenting_ministers.append(c.from_minister)

        # 4. Quality score: weighted average of contributors
        weights = [1.0 / len(successes)] * len(successes)
        if votes and winner:
            total_weight = sum(v.weight for v in votes)
            if total_weight > 0:
                weights_map = {
                    m.minister: sum(v.weight for v in votes if v.option == m.minister) / total_weight
                    for m in successes
                }
                weights = [
                    weights_map.get(m.minister, 1.0 / len(successes))
                    for m in successes
                ]

        quality = sum(
            w * m.confidence
            for w, m in zip(weights, successes)
        ) / max(1, sum(weights))

        draft = SynthesisDraft(
            version=1,
            content="\n".join(parts),
            base_minister=winner,
            incorporated_from=augmented,
            critiques_applied=sum(
                1 for c in critiques
                if c.direction == CritiqueDirection.REFINE
            ),
            quality_score=quality,
            issues=dissenting_ministers,
        )

        return draft

    def _elect_winner(self, votes: list[Vote], successes: list[Any]) -> str:
        """Elect the winning minister from votes."""
        if not votes:
            # Fallback: highest confidence minister
            return max(successes, key=lambda m: m.confidence).minister

        tally: dict[str, float] = {}
        for v in votes:
            tally[v.option] = tally.get(v.option, 0.0) + v.weight

        if tally:
            return max(tally, key=tally.get)

        return successes[0].minister

    def _extract_unique_portion(self, text: str, base_text: str) -> str:
        """Extract the portion of text that is unique compared to base."""
        base_sentences = set(base_text.split("。"))
        text_sentences = text.split("。")
        unique = [s for s in text_sentences if s.strip() and s.strip() not in base_sentences]
        if not unique:
            # Fallback: return a summary of the text
            return text[:200] + ("..." if len(text) > 200 else "")
        return "。".join(unique[:5])

    # ------------------------------------------------------------------
    # Phase 4: Self-Reflection
    # ------------------------------------------------------------------

    def _self_reflect(
        self,
        intent: str,
        draft: SynthesisDraft,
        memorials: list[Any],
        critiques: list[Critique],
    ) -> SynthesisDraft:
        """Critique the current draft and produce an improved version.

        Self-reflection checks:
        1. Are all critiques addressed?
        2. Are there contradictions in the synthesis?
        3. Can the structure be improved?
        4. Is the quality score justified?
        """
        issues: list[str] = []
        improvements: list[str] = []

        # Check 1: Unaddressed critiques
        unaddressed = [
            c for c in critiques
            if c.direction == CritiqueDirection.CHALLENGE
            and c.to_minister == draft.base_minister
        ]
        if unaddressed:
            issues.append(f"有 {len(unaddressed)} 条质疑未被充分回应")
            improvements.append("应考虑反对意见并给出明确回应")

        # Check 2: Structural quality
        if draft.content.count("\n") < 2:
            issues.append("圣旨结构过于扁平，缺乏层次")
            improvements.append("增加分段和编号结构")

        # Check 3: Contradictions
        # Simple check: look for conflicting keywords
        conf_pairs = [
            ("推荐", "不推荐"),
            ("支持", "反对"),
            ("采用", "弃用"),
        ]
        for pos, neg in conf_pairs:
            if pos in draft.content and neg in draft.content:
                issues.append(f"可能存在矛盾：同时出现'{pos}'和'{neg}'")
                improvements.append("明确最终立场，消除内部矛盾")

        # Check 4: Length vs quality alignment
        if len(draft.content) < 200 and draft.quality_score > 0.8:
            issues.append("高质量但篇幅过短，可能遗漏细节")
            improvements.append("补充具体实施细节或案例")

        # Adjust quality based on issues found
        penalty = min(0.25, len(issues) * 0.08)
        new_quality = max(0.1, draft.quality_score - penalty)

        # Build improved content
        improved_content = draft.content
        if improvements:
            # Append improvements as a note (in production, this would re-generate)
            improved_content += "\n\n--- 修订说明 ---\n"
            for i, imp in enumerate(improvements, 1):
                improved_content += f"{i}. {imp}\n"

            # If we addressed issues, quality improves
            new_quality = min(draft.quality_score + 0.05, 0.95)

        return SynthesisDraft(
            version=draft.version + 1,
            content=improved_content,
            base_minister=draft.base_minister,
            incorporated_from=draft.incorporated_from,
            critiques_applied=draft.critiques_applied + len(improvements),
            quality_score=new_quality,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # Final computations
    # ------------------------------------------------------------------

    def _compute_final_confidence(
        self,
        memorials: list[Any],
        draft: SynthesisDraft,
    ) -> float:
        """Compute final confidence score for the decree.

        Weighted by: quality score (50%) + minister confidence avg (30%)
        + merit-based boost (20%).
        """
        successes = [m for m in memorials if m.success]
        if not successes:
            return draft.quality_score * 0.5

        avg_conf = sum(m.confidence for m in successes) / len(successes)

        merit_boost = 1.0
        if self._merit_board is not None:
            draft_merit = self._merit_board.compute_merit(draft.base_minister)
            merit_boost = min(1.2, max(0.8, draft_merit / 75.0))

        confidence = (
            draft.quality_score * 0.5
            + avg_conf * 0.3
            + merit_boost * 0.2
        )
        return min(1.0, max(0.0, confidence))

    def _collect_dissenting(
        self,
        critiques: list[Critique],
        votes: list[Vote],
        draft: SynthesisDraft,
    ) -> list[str]:
        """Collect unresolved dissenting opinions."""
        dissenting: list[str] = []

        # Challenging critiques against the winner
        for c in critiques:
            if (
                c.direction == CritiqueDirection.CHALLENGE
                and c.to_minister == draft.base_minister
            ):
                dissenting.append(
                    f"{c.from_minister} 挑战 {c.to_minister}: "
                    f"{'; '.join(c.points[:2])}"
                )

        # Votes for non-winners
        winner = draft.base_minister
        for v in votes:
            if v.option != winner and v.weight > 0.5:
                dissenting.append(
                    f"{v.voter} 投票支持 {v.option}（而非最终采纳的 {winner}）"
                )

        return dissenting[:5]  # Cap at 5

    def _determine_winner(
        self,
        memorials: list[Any],
        votes: list[Vote],
        draft: SynthesisDraft,
    ) -> str:
        """Determine which minister's output is the primary contributor."""
        # Already determined during draft synthesis
        return draft.base_minister
