"""
Censorate (御史台) — independent quality oversight for the Imperial Court.

Inspired by:
    - Chinese imperial 御史台 (Censorate): independent from the bureaucracy,
      reported directly to the Emperor, could impeach any official
    - Roman Tribune of the Plebs: power of veto (intercessio) over any
      magistrate's action, sacrosanct
    - Modern Inspector General / Ombudsman models

The Censorate is a special 9th minister that:
    1. Never generates content — only critiques
    2. Reviews every memorial (大臣奏章) and the final decree (圣旨)
    3. Can issue a VETO on low-quality outputs
    4. Assigns quality scores that feed into MeritBoard
    5. Tracks systemic issues (patterns of failure across ministers)

Key principle: the Censorate's power comes from independence.
It cannot be overruled by other ministers — only the Emperor (or user feedback)
can override a veto.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger("jarvis.court.censorate")


class CensorVerdict(Enum):
    """The Censorate's judgment on a piece of output."""
    APPROVED = auto()          # Passes all quality checks
    QUALIFIED = auto()         # Acceptable with minor issues
    FLAGGED = auto()           # Has significant issues, must be reviewed
    VETOED = auto()            # Rejected outright, must be redone


@dataclass
class CensorReview:
    """A single review by the Censorate."""
    review_id: str
    target: str                   # "memorial:chancellor" or "decree:decree_1"
    verdict: CensorVerdict
    quality_score: float          # 0.0 - 1.0
    issues: list[str]             # Specific problems found
    strengths: list[str]          # What was done well
    recommendation: str           # What should be done
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class CensorReport:
    """Periodic/systemic report from the Censorate.

    Tracks patterns: which ministers consistently produce issues,
    what types of errors recur, systemic weaknesses.
    """
    reviews: list[CensorReview]
    systemic_issues: list[str]
    risk_assessment: str             # Overall court quality assessment
    top_offenders: list[str]         # Ministers with most vetoes
    quality_trend: str               # "improving" / "stable" / "declining"
    total_veto_count: int
    total_review_count: int


class Censorate:
    """The Imperial Censorate — independent quality watchdog.

    Usage:
        censor = Censorate()

        # Review a memorial
        review = await censor.review_memorial(
            minister="丞相",
            intent="分析代码漏洞",
            output="...",
            confidence=0.85,
        )
        if review.verdict == CensorVerdict.VETOED:
            # Minister must redo

        # Review the final decree
        decree_review = await censor.review_decree(
            decree_output="...",
            memorials_reviews=[review1, review2],
        )

        # Get systemic report
        report = censor.get_systemic_report()
    """

    # Quality thresholds
    MIN_QUALITY_THRESHOLD = 0.4       # Below this → FLAGGED
    VETO_THRESHOLD = 0.25             # Below this → VETOED
    EXCELLENCE_THRESHOLD = 0.85       # Above this → APPROVED with praise

    def __init__(self) -> None:
        self._reviews: list[CensorReview] = []
        self._veto_count: int = 0
        self._minister_vetoes: dict[str, int] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Memorial review (审查奏章)
    # ------------------------------------------------------------------

    async def review_memorial(
        self,
        minister: str,
        intent: str,
        output: str,
        confidence: float,
        execution_time_ms: float = 0.0,
    ) -> CensorReview:
        """Review a single minister's memorial (奏章).

        Evaluates:
            1. Output quality — is it substantive or hollow?
            2. Confidence alignment — does confidence match output quality?
            3. Relevance — does the output address the intent?
            4. Completeness — is the output complete or truncated?
        """
        review_id = f"censor_mem_{len(self._reviews) + 1}"

        issues: list[str] = []
        strengths: list[str] = []

        # Heuristic quality checks (model-enhanced in production)
        quality_score = await self._assess_quality(
            intent=intent,
            output=output,
            confidence=confidence,
            execution_time_ms=execution_time_ms,
            issues=issues,
            strengths=strengths,
        )

        # Determine verdict
        verdict = self._determine_verdict(quality_score, issues)

        async with self._lock:
            review = CensorReview(
                review_id=review_id,
                target=f"memorial:{minister}",
                verdict=verdict,
                quality_score=quality_score,
                issues=issues,
                strengths=strengths,
                recommendation=self._build_recommendation(verdict, issues),
            )
            self._reviews.append(review)

            if verdict == CensorVerdict.VETOED:
                self._veto_count += 1
                self._minister_vetoes[minister] = (
                    self._minister_vetoes.get(minister, 0) + 1
                )

        logger.info(
            "[Censorate] Reviewed %s: verdict=%s quality=%.2f issues=%d",
            minister, verdict.name, quality_score, len(issues),
        )
        return review

    async def _assess_quality(
        self,
        intent: str,
        output: str,
        confidence: float,
        execution_time_ms: float,
        issues: list[str],
        strengths: list[str],
    ) -> float:
        """Heuristic quality assessment. In production, this uses a real LLM.

        Checks:
        - Empty / trivial output
        - Output length proportionality
        - Confidence alignment
        - Keyword relevance
        """
        # Empty or trivially short output
        if not output or len(output.strip()) < 10:
            issues.append("输出内容过短或无实质内容")
            return 0.1

        if len(output.strip()) < 50:
            issues.append("输出内容不够详尽")

        # Confidence misalignment: high confidence but short output
        if confidence > 0.8 and len(output) < 100:
            issues.append(f"置信度({confidence:.0%})与输出长度({len(output)}字)不匹配")

        # Overconfidence for mediocre output
        if confidence > 0.9 and len(output) < 200:
            issues.append("可能存在过度自信")

        # Word salad / garbage detection
        common_garbage = ["as an ai", "i cannot", "i'm unable", "i apologize"]
        output_lower = output.lower()
        garbage_hits = sum(1 for g in common_garbage if g in output_lower)
        if garbage_hits >= 2:
            issues.append("输出包含AI道歉/拒绝模式，可能未完成任务")
            return 0.15

        # Relevance: check if key intent words appear in output.
        # For CJK (Chinese), do character-level bigram extraction since
        # there are no spaces between words.
        intent_lower = intent.lower()

        # Extract bigrams from CJK text for matching
        cjk_chars = [c for c in intent_lower if '\u4e00' <= c <= '\u9fff']
        if len(cjk_chars) >= 2:
            bigrams = {''.join(cjk_chars[i:i+2]) for i in range(len(cjk_chars)-1)}
            # Also add 3-grams for longer intents
            trigrams = {''.join(cjk_chars[i:i+3]) for i in range(len(cjk_chars)-2)}
            all_grams = bigrams | trigrams
            if all_grams:
                matched = sum(1 for g in all_grams if g in output_lower)
                relevance = matched / len(all_grams)
                if relevance < 0.3:
                    issues.append(f"输出与意图相关性低({relevance:.0%})")
                elif relevance > 0.6:
                    strengths.append(f"输出与意图高度相关({relevance:.0%})")
        else:
            # English-style: word-level matching
            intent_words = set(intent_lower.split())
            stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "on",
                         "at", "to", "for", "of", "and", "or", "it", "that", "this"}
            intent_keywords = {w for w in intent_words if len(w) > 1 and w not in stop_words}
            if intent_keywords:
                matched = sum(1 for kw in intent_keywords if kw in output_lower)
                relevance = matched / len(intent_keywords)
                if relevance < 0.3:
                    issues.append(f"输出与意图相关性低({relevance:.0%})")
                elif relevance > 0.7:
                    strengths.append(f"输出与意图高度相关({relevance:.0%})")

        # Substantial output
        if len(output) > 500:
            strengths.append("输出内容充实详细")
        if len(output) > 200:
            strengths.append("输出长度合理")

        # Compute base quality
        if not issues:
            base_quality = 0.85
        elif len(issues) == 1:
            base_quality = 0.65
        elif len(issues) == 2:
            base_quality = 0.45
        else:
            base_quality = 0.25

        # Adjust by confidence alignment
        # If confidence is high, slight boost; if low, slight penalty
        conf_adjust = (confidence - 0.5) * 0.15
        quality = max(0.0, min(1.0, base_quality + conf_adjust))

        return quality

    def _determine_verdict(
        self, quality_score: float, issues: list[str]
    ) -> CensorVerdict:
        """Map quality score to verdict."""
        if quality_score < self.VETO_THRESHOLD:
            return CensorVerdict.VETOED
        if quality_score < self.MIN_QUALITY_THRESHOLD:
            return CensorVerdict.FLAGGED
        if quality_score >= self.EXCELLENCE_THRESHOLD:
            return CensorVerdict.APPROVED
        return CensorVerdict.QUALIFIED

    def _build_recommendation(
        self, verdict: CensorVerdict, issues: list[str]
    ) -> str:
        """Build actionable recommendation based on verdict."""
        if verdict == CensorVerdict.VETOED:
            return f"驳回重做。问题: {'; '.join(issues[:3])}"
        if verdict == CensorVerdict.FLAGGED:
            return f"需修正后重审。问题: {'; '.join(issues[:2])}"
        if verdict == CensorVerdict.QUALIFIED:
            return "可接受，建议优化细节"
        return "准奏，品质优良"

    # ------------------------------------------------------------------
    # Decree review (审核圣旨)
    # ------------------------------------------------------------------

    async def review_decree(
        self,
        intent: str,
        decree_output: str,
        memorial_reviews: list[CensorReview],
        confidence: float,
    ) -> CensorReview:
        """Review the Emperor's final decree (圣旨).

        The decree review is more lenient than individual memorial review
        because the Emperor has already synthesized — but the Censorate
        can still flag or veto if the synthesis is clearly poor.
        """
        review_id = f"censor_decree_{len(self._reviews) + 1}"

        issues: list[str] = []
        strengths: list[str] = []

        # Check if decree is just a concatenation without synthesis
        if decree_output.count("【") >= 3 and len(decree_output) < 800:
            issues.append("圣旨疑似简单拼接，缺乏综合研判")

        # Check if any memorial vetoes were ignored
        vetoed_ministers = [
            r.target.replace("memorial:", "")
            for r in memorial_reviews
            if r.verdict == CensorVerdict.VETOED
        ]
        if vetoed_ministers:
            issues.append(
                f"以下大臣奏章已被驳回但仍纳入圣旨: {', '.join(vetoed_ministers)}"
            )

        # Check synthesis quality
        quality_score = await self._assess_quality(
            intent=intent,
            output=decree_output,
            confidence=confidence,
            execution_time_ms=0.0,
            issues=issues,
            strengths=strengths,
        )

        # Decree gets a +0.1 leniency bonus (Emperor's authority)
        quality_score = min(1.0, quality_score + 0.1)

        verdict = self._determine_verdict(quality_score, issues)

        async with self._lock:
            review = CensorReview(
                review_id=review_id,
                target=f"decree:{review_id}",
                verdict=verdict,
                quality_score=quality_score,
                issues=issues,
                strengths=strengths,
                recommendation=self._build_recommendation(verdict, issues),
            )
            self._reviews.append(review)

        return review

    # ------------------------------------------------------------------
    # Systemic analysis (系统性问题分析)
    # ------------------------------------------------------------------

    def get_systemic_report(self) -> CensorReport:
        """Generate a systemic quality report for the entire court.

        Identifies patterns: which ministers consistently fail,
        what types of errors recur, and the overall health of the court.
        """
        if not self._reviews:
            return CensorReport(
                reviews=[],
                systemic_issues=[],
                risk_assessment="尚无数据，无法评估",
                top_offenders=[],
                quality_trend="stable",
                total_veto_count=0,
                total_review_count=0,
            )

        # Count vetoes per minister
        veto_counts: dict[str, int] = {}
        for r in self._reviews:
            if r.verdict == CensorVerdict.VETOED:
                name = r.target.replace("memorial:", "").replace("decree:", "")
                veto_counts[name] = veto_counts.get(name, 0) + 1

        # Top offenders (most vetoes)
        top_offenders = sorted(
            veto_counts.items(), key=lambda x: x[1], reverse=True
        )[:3]
        top_offender_names = [name for name, _ in top_offenders]

        # Systemic issues: aggregate common issues
        issue_counts: dict[str, int] = {}
        for r in self._reviews:
            for issue in r.issues:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
        systemic = [
            issue for issue, count in
            sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        ]

        # Quality trend: compare recent vs older reviews
        recent_reviews = self._reviews[-20:]
        older_reviews = self._reviews[:-20] if len(self._reviews) > 20 else []
        recent_avg = (
            sum(r.quality_score for r in recent_reviews) / len(recent_reviews)
        )
        if older_reviews:
            older_avg = (
                sum(r.quality_score for r in older_reviews)
                / len(older_reviews)
            )
            delta = recent_avg - older_avg
            if delta > 0.1:
                trend = "improving"
            elif delta < -0.1:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Risk assessment
        veto_rate = self._veto_count / max(1, len(self._reviews))
        if veto_rate > 0.3:
            risk = "高危：超过30%的审查被否决，朝堂质量严重下滑"
        elif veto_rate > 0.15:
            risk = "中危：否决率偏高，需关注大臣输出质量"
        elif len(systemic) >= 3:
            risk = "低危：存在系统性问题，建议针对性优化"
        else:
            risk = "健康：朝堂运作正常，质量可控"

        return CensorReport(
            reviews=self._reviews,
            systemic_issues=systemic,
            risk_assessment=risk,
            top_offenders=top_offender_names,
            quality_trend=trend,
            total_veto_count=self._veto_count,
            total_review_count=len(self._reviews),
        )

    # ------------------------------------------------------------------
    # Veto management
    # ------------------------------------------------------------------

    def get_minister_veto_count(self, minister: str) -> int:
        """How many times has a minister been vetoed?"""
        return self._minister_vetoes.get(minister, 0)

    def is_minister_at_risk(self, minister: str) -> bool:
        """Is this minister at risk of demotion/elimination?

        Criteria: 3+ vetoes in the last 10 reviews for this minister.
        """
        recent = [
            r for r in self._reviews[-10:]
            if r.target == f"memorial:{minister}"
            and r.verdict == CensorVerdict.VETOED
        ]
        return len(recent) >= 3

    @property
    def total_vetoes(self) -> int:
        return self._veto_count

    @property
    def total_reviews(self) -> int:
        return len(self._reviews)
