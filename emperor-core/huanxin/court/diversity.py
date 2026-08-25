"""
DiversityMonitor + Catastrophe (基因多样性监控 + 大灾变)

Monitors population genetic diversity to detect convergence / stagnation.
When diversity drops below a critical threshold for consecutive cycles,
triggers a "catastrophe" — mass extinction of the monoculture, followed
by rapid re-diversification through high-mutation cloning and fresh
specialist spawning.

Inspired by:
    - Mass extinction events in natural evolution (Permian-Triassic, K-Pg)
    - Evolutionary rescue in population genetics
    - Novelty search in quality-diversity algorithms (MAP-Elites)
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("huanxin.court.diversity")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DiversitySnapshot:
    """A point-in-time measurement of court genetic diversity."""
    timestamp: str
    score: float               # 0.0–1.0, higher = more diverse
    genomic_similarity: float   # Average pairwise cosine similarity (0–1)
    merit_variance: float       # Variance of merit scores
    active_count: int
    in_crisis: bool             # Was this cycle in crisis?


@dataclass
class CatastropheReport:
    """Record of a diversity catastrophe event."""
    timestamp: str
    trigger_score: float        # Diversity score that triggered it
    eliminated_count: int       # Ministers purged
    survivors: list[str]        # Who survived
    spawned: list[str]          # New ministers created
    crisis_streak: int          # How many cycles of crisis before trigger
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DiversityMonitor
# ---------------------------------------------------------------------------

class DiversityMonitor:
    """Watches court genetic diversity and triggers catastrophe if needed.

    When all ministers converge to nearly identical "gene" values
    (temperature, confidence, exploration, etc.), the court loses its
    collective intelligence — everyone gives similar answers. This is
    the evolutionary equivalent of groupthink.

    The monitor measures pairwise cosine similarity between the feature
    vectors of all active ministers. High average similarity → low
    diversity → crisis brewing.

    Catastrophe re-seeds diversity by:
      1. Keeping only the top 2–3 performers
      2. Eliminating everyone else (archiving their genomes)
      3. High-mutation cloning survivors (3× normal mutation scale)
      4. Injecting domain specialists for uncovered areas
    """

    # Below this score, population is in crisis
    DIVERSITY_CRISIS_THRESHOLD = 0.15

    # Consecutive cycles below threshold trigger catastrophe
    CRISIS_STREAK_LIMIT = 5

    # Minimum cycles between catastrophes (cooldown)
    CATASTROPHE_COOLDOWN = 20

    # Survivors to keep during catastrophe
    CATASTROPHE_SURVIVORS = 3

    # Clones to generate from each survivor
    CLONES_PER_SURVIVOR = 2

    # Fresh domain specialists to inject
    SPECIALISTS_TO_SPAWN = 2

    def __init__(self) -> None:
        self.history: list[DiversitySnapshot] = []  # type: ignore[arg-type]
        self.catastrophes: list[CatastropheReport] = []  # type: ignore[arg-type]
        self._crisis_streak: int = 0
        self._last_catastrophe_cycle: int = -self.CATASTROPHE_COOLDOWN
        self._last_genomic_diversity: float = 1.0

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------

    def measure(
        self,
        genomes: dict[str, Any],
        merit_scores: dict[str, float],
        active_names: list[str],
    ) -> DiversitySnapshot:
        """Measure current genetic diversity of the court.

        Args:
            genomes: {name: MinisterGenome} mapping.
            merit_scores: {name: merit_value} mapping.
            active_names: list of currently active minister names.

        Returns a DiversitySnapshot with the composite score.
        """
        feature_vectors: list[tuple[str, list[float]]] = []
        for name in active_names:
            g = genomes.get(name)
            if g is None:
                continue
            vec = self._extract_feature_vector(g)
            feature_vectors.append((name, vec))

        n = len(feature_vectors)
        if n < 2:
            # Cannot measure diversity with <2 ministers
            snap = DiversitySnapshot(
                timestamp=datetime.now(timezone.utc).isoformat(),
                score=1.0,
                genomic_similarity=0.0,
                merit_variance=0.0,
                active_count=n,
                in_crisis=False,
            )
            self.history.append(snap)
            self._crisis_streak = 0
            return snap

        # Compute pairwise cosine similarity
        similarities: list[float] = []
        for i in range(n):
            for j in range(i + 1, n):
                sim = self._cosine_similarity(
                    feature_vectors[i][1],
                    feature_vectors[j][1],
                )
                similarities.append(sim)

        avg_similarity = sum(similarities) / len(similarities)
        # Diversity = 1 - similarity. High similarity = low diversity.
        genomic_diversity = max(0.0, 1.0 - avg_similarity)

        # Merit variance: high variance = good (different skill levels)
        merits = [merit_scores.get(name, 0.0) for name in active_names]
        if len(merits) >= 2:
            mean_merit = sum(merits) / len(merits)
            merit_var = sum((m - mean_merit) ** 2 for m in merits) / len(merits)
            # Normalize to 0–1 range (arbitrary cap at 400 for sensible scaling)
            merit_diversity = min(1.0, math.sqrt(merit_var) / 20.0)
        else:
            merit_diversity = 0.5

        # Composite score: 70% genomic + 30% merit. This composite is what the
        # public ``in_crisis`` flag and ``score`` field report, so historical
        # semantics (and existing tests) are preserved.
        composite = 0.7 * genomic_diversity + 0.3 * merit_diversity
        composite = max(0.0, min(1.0, composite))

        # Groupthink is fundamentally a *genetic* problem. Previously the crisis
        # escalation streak was driven by the composite score, but merit noise
        # kept pushing the composite above the threshold on some cycles, which
        # reset ``_crisis_streak`` before ``CRISIS_STREAK_LIMIT`` was ever
        # reached — stranding a homogeneous court in perpetual "[Diversity]
        # Crisis" with no recovery path. So we gate the *escalation streak* on
        # genomic diversity only; ``in_crisis``/``score`` stay composite-driven.
        self._last_genomic_diversity = genomic_diversity
        in_crisis = composite < self.DIVERSITY_CRISIS_THRESHOLD
        if genomic_diversity < self.DIVERSITY_CRISIS_THRESHOLD:
            self._crisis_streak += 1
        else:
            self._crisis_streak = 0

        snap = DiversitySnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            score=composite,
            genomic_similarity=avg_similarity,
            merit_variance=merit_diversity,
            active_count=n,
            in_crisis=in_crisis,
        )
        self.history.append(snap)

        if self._crisis_streak > 0:
            logger.warning(
                "[Diversity] Crisis cycle %d/%d (genomic_diversity=%.3f, "
                "similarity=%.3f, composite=%.3f)",
                self._crisis_streak,
                self.CRISIS_STREAK_LIMIT,
                genomic_diversity,
                avg_similarity,
                composite,
            )

        return snap

    # ------------------------------------------------------------------
    # Crisis detection
    # ------------------------------------------------------------------

    def is_catastrophe_needed(self, cycle_count: int) -> bool:
        """Check if a catastrophe should be triggered.

        Returns True only if:
          1. Cool-down period has elapsed since last catastrophe.
          2. We've had enough consecutive crisis cycles.
        """
        if cycle_count - self._last_catastrophe_cycle < self.CATASTROPHE_COOLDOWN:
            return False
        return self._crisis_streak >= self.CRISIS_STREAK_LIMIT

    # ------------------------------------------------------------------
    # Catastrophe execution
    # ------------------------------------------------------------------

    def plan_catastrophe(
        self,
        genomes: dict[str, Any],
        merit_scores: dict[str, float],
        active_names: list[str],
        all_names: list[str],
        cycle_count: int = 0,
    ) -> CatastropheReport:
        """Plan a catastrophe: determine who lives, who dies, who spawns.

        Does NOT modify external state — returns a plan for the caller
        to execute. This separation makes testing easier.

        Returns a CatastropheReport describing the plan.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Pick survivors: top N by merit
        ranked = sorted(
            active_names,
            key=lambda n: merit_scores.get(n, 0.0),
            reverse=True,
        )
        survivors = ranked[:self.CATASTROPHE_SURVIVORS]
        eliminated = [n for n in active_names if n not in survivors]

        # Domains already covered by survivors
        covered_domains: set[str] = set()
        for name in survivors:
            g = genomes.get(name)
            if g and hasattr(g, "domain"):
                covered_domains.add(g.domain)

        # Candidate specialists: domains not yet covered
        all_domains = {
            "writing", "code", "research", "search", "multimodal",
            "finance", "science", "security",
        }
        missing = sorted(all_domains - covered_domains)

        specialist_names: list[str] = []
        for i in range(min(self.SPECIALISTS_TO_SPAWN, len(missing))):
            spec_name = f"新晋_{missing[i]}"
            existing = {n for n in all_names if n.startswith(spec_name)}
            if not existing:
                specialist_names.append(spec_name)

        # Clone names
        clone_names: list[str] = []
        for survivor in survivors:
            for j in range(self.CLONES_PER_SURVIVOR):
                clone_names.append(f"{survivor}_新生{j + 1}")

        report = CatastropheReport(
            timestamp=now,
            trigger_score=self.history[-1].score if self.history else 0.0,
            eliminated_count=len(eliminated),
            survivors=list(survivors),
            spawned=clone_names + specialist_names,
            crisis_streak=self._crisis_streak,
            details={
                "eliminated": eliminated,
                "clones": clone_names,
                "specialists": specialist_names,
                "covered_domains": sorted(covered_domains),
                "missing_domains": missing,
            },
        )
        self.catastrophes.append(report)

        # Reset crisis tracking
        self._crisis_streak = 0
        # Fix: the cooldown gate must be measured in *cycles*, not active_count.
        # Callers that wire the real evolution cycle pass cycle_count; legacy
        # callers (and tests) that don't fall back to the previous behavior.
        self._last_catastrophe_cycle = (
            cycle_count if cycle_count > 0
            else (self.history[-1].active_count if self.history else 0)
        )

        logger.critical(
            "[Diversity] CATASTROPHE! Eliminating %d ministers. "
            "Survivors: %s. Spawning %d new.",
            len(eliminated), survivors, len(report.spawned),
        )

        return report

    # ------------------------------------------------------------------
    # Feature vector extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_feature_vector(genome: Any) -> list[float]:
        """Extract a normalized feature vector from a MinisterGenome."""
        return [
            genome.temperature,
            genome.confidence_baseline,
            genome.exploration_rate,
            genome.conservatism,
            genome.prompt_mutation_rate,
            genome.specialization_weight,
        ]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # ------------------------------------------------------------------
    # History query
    # ------------------------------------------------------------------

    def get_latest_score(self) -> float:
        """Return the most recent diversity score, or 1.0 if no history."""
        if self.history:
            return self.history[-1].score
        return 1.0

    def get_latest_genomic_diversity(self) -> float:
        """Return the most recent *genomic* diversity (1 - mean cosine sim).

        Unlike :meth:`get_latest_score` (which blends noisy merit variance),
        this reflects pure genetic groupthink and is what the breeding loop and
        crisis gate should react to.
        """
        return self._last_genomic_diversity

    def get_crisis_streak(self) -> int:
        """Return current crisis streak length."""
        return self._crisis_streak

    def get_catastrophe_count(self) -> int:
        """Return total number of catastrophes triggered."""
        return len(self.catastrophes)
