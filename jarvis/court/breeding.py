"""
AutoBreeder (自动育种器) — proactive minister breeding engine.

While SurvivalMechanism reacts to failures (eliminate → clone), AutoBreeder
anticipates needs before they become critical.  It reads three signal streams:

    1. Capability Gap    — domains with weak or missing minister coverage
    2. Diversity Heatmap — gene regions where the population is too homogeneous
    3. Merit Trend       — ministers whose sliding-window merit is declining

From these signals, AutoBreeder decides:
    - WHAT to breed  (target domain + desired traits)
    - HOW to breed   (strategy: SPECIALIZE / EXPLORE / SPECIALIST)
    - WHEN to breed  (cool-down prevents flooding)

The result: the court continuously generates high-quality candidates
tailored to its evolving needs, instead of relying on random mutation
after failures.

Architecture:
    AutoBreeder
      ├── GapAnalyzer      → capability gap detection
      ├── StrategySelector → choose breeding strategy per gap
      └── GenomeGenerator  → produce targeted genomes
"""

from __future__ import annotations

import copy
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger("jarvis.court.breeding")


# ── Breeding Strategies ────────────────────────────────────────────

class BreedingStrategy(Enum):
    """How to create a new minister genome."""
    SPECIALIZE = auto()    # Clone elite + mutate toward target domain
    EXPLORE = auto()       # Random genome with high diversity exploration
    SPECIALIST = auto()    # Targeted domain expert with preset traits
    HYBRID = auto()        # Crossover two diverse ministers


# ── Data Classes ────────────────────────────────────────────────────

@dataclass
class CapabilityGap:
    """A detected weakness in the court's capability coverage."""
    domain: str
    severity: float          # 0–1, how urgent the gap is
    reason: str              # human-readable explanation
    ministers_present: int   # how many cover this domain
    avg_merit: float         # average merit of covering ministers
    diversity_score: float   # gene diversity in this domain's genome region


@dataclass
class BreedingCandidate:
    """A proposed new minister genome to breed."""
    target_domain: str
    strategy: BreedingStrategy
    genome_template: Optional[dict[str, float]]  # preset genome values
    parent_minister: Optional[str]  # source for SPECIALIZE strategy
    reasoning: str


@dataclass
class BreedingReport:
    """Result of a breeding cycle."""
    gaps_detected: list[CapabilityGap]
    candidates_proposed: list[BreedingCandidate]
    candidates_created: list[str]  # names of actually created ministers
    strategies_used: dict[BreedingStrategy, int]
    timestamp: str


@dataclass
class BreedingOutcome:
    """Outcome of a bred minister after evolution cycles have passed."""

    minister_name: str
    domain: str
    strategy: BreedingStrategy
    survived: bool          # did they survive to end of observation window?
    promoted: bool          # did they get promoted from shadow → active?
    max_merit: float        # peak merit score achieved
    cycles_survived: int    # how many cycles before eliminated (or window length)
    final_status: str       # "ACTIVE" | "SHADOW" | "ELIMINATED"
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))


class StrategyPerformanceTracker:
    """Tracks breeding outcomes to compute per-strategy effectiveness.

    Maintains a sliding window of recent outcomes, computing success rates
    that feed back into StrategySelector's adaptive weights.
    """

    MAX_OUTCOMES = 200         # max outcomes to retain in memory
    SUCCESS_WINDOW = 50        # recent N outcomes for rate calculation
    MIN_SAMPLES = 5            # minimum samples before adapting weights
    WEIGHT_LEARNING_RATE = 0.15  # how fast weights adapt to new evidence

    def __init__(self) -> None:
        self._outcomes: list[BreedingOutcome] = []
        self._strategy_stats: dict[BreedingStrategy, dict[str, float]] = {
            s: {"successes": 0, "total": 0, "sum_merit": 0.0, "sum_cycles": 0}
            for s in BreedingStrategy
        }

    def record(self, outcome: BreedingOutcome) -> None:
        """Record a breeding outcome and update statistics."""
        self._outcomes.append(outcome)
        if len(self._outcomes) > self.MAX_OUTCOMES:
            self._outcomes = self._outcomes[-self.MAX_OUTCOMES:]

        # Update aggregate stats
        stats = self._strategy_stats[outcome.strategy]
        stats["total"] += 1
        stats["sum_merit"] += outcome.max_merit
        stats["sum_cycles"] += outcome.cycles_survived
        if outcome.survived:
            stats["successes"] += 1

    def get_success_rate(self, strategy: BreedingStrategy) -> float:
        """Sliding-window success rate for a given strategy."""
        recent = [
            o for o in self._outcomes[-self.SUCCESS_WINDOW:]
            if o.strategy == strategy
        ]
        if len(recent) < self.MIN_SAMPLES:
            return 0.5  # neutral prior
        survived = sum(1 for o in recent if o.survived)
        return survived / len(recent)

    def get_avg_merit(self, strategy: BreedingStrategy) -> float:
        """Average peak merit achieved by this strategy."""
        recent = [
            o for o in self._outcomes[-self.SUCCESS_WINDOW:]
            if o.strategy == strategy
        ]
        if not recent:
            return 0.0
        return sum(o.max_merit for o in recent) / len(recent)

    def get_avg_cycles(self, strategy: BreedingStrategy) -> float:
        """Average cycles survived by this strategy."""
        recent = [
            o for o in self._outcomes[-self.SUCCESS_WINDOW:]
            if o.strategy == strategy
        ]
        if not recent:
            return 0.0
        return sum(o.cycles_survived for o in recent) / len(recent)

    def get_promotion_rate(self, strategy: BreedingStrategy) -> float:
        """Fraction of bred ministers that got promoted to ACTIVE."""
        recent = [
            o for o in self._outcomes[-self.SUCCESS_WINDOW:]
            if o.strategy == strategy
        ]
        if len(recent) < self.MIN_SAMPLES:
            return 0.5
        promoted = sum(1 for o in recent if o.promoted)
        return promoted / len(recent)

    def get_composite_score(self, strategy: BreedingStrategy) -> float:
        """Composite 0-1 score: 40% survival + 30% merit + 20% promotion + 10% longevity."""
        survival = self.get_success_rate(strategy)
        merit = min(self.get_avg_merit(strategy) / 100.0, 1.0)
        promotion = self.get_promotion_rate(strategy)
        cycles = min(self.get_avg_cycles(strategy) / 20.0, 1.0)
        return survival * 0.40 + merit * 0.30 + promotion * 0.20 + cycles * 0.10

    def get_all_scores(self) -> dict[BreedingStrategy, float]:
        """Composite scores for all strategies."""
        return {s: self.get_composite_score(s) for s in BreedingStrategy}

    def get_outcome_count(self) -> int:
        return len(self._outcomes)

    def reset(self) -> None:
        """Clear all tracked outcomes."""
        self._outcomes.clear()
        for stats in self._strategy_stats.values():
            stats["successes"] = 0
            stats["total"] = 0
            stats["sum_merit"] = 0.0
            stats["sum_cycles"] = 0


# ── Domain definitions ──────────────────────────────────────────────

KNOWN_DOMAINS = [
    "engineering", "research", "security", "finance",
    "personal", "health", "home", "general", "core",
    "creative", "legal", "education", "entertainment",
]

# Domain → ideal genome profile (temperature, confidence_baseline,
# creativity, thoroughness, speed, social_intelligence)
DOMAIN_PROFILES: dict[str, dict[str, float]] = {
    "engineering": {"temperature": 0.3, "confidence_baseline": 0.85,
                    "creativity": 0.2, "thoroughness": 0.9,
                    "speed": 0.6, "social_intelligence": 0.2},
    "research":    {"temperature": 0.6, "confidence_baseline": 0.75,
                    "creativity": 0.8, "thoroughness": 0.7,
                    "speed": 0.4, "social_intelligence": 0.3},
    "security":    {"temperature": 0.2, "confidence_baseline": 0.90,
                    "creativity": 0.1, "thoroughness": 0.95,
                    "speed": 0.7, "social_intelligence": 0.1},
    "finance":     {"temperature": 0.25, "confidence_baseline": 0.88,
                    "creativity": 0.1, "thoroughness": 0.9,
                    "speed": 0.5, "social_intelligence": 0.3},
    "creative":    {"temperature": 0.85, "confidence_baseline": 0.60,
                    "creativity": 0.95, "thoroughness": 0.4,
                    "speed": 0.6, "social_intelligence": 0.7},
    "legal":       {"temperature": 0.15, "confidence_baseline": 0.92,
                    "creativity": 0.05, "thoroughness": 0.98,
                    "speed": 0.3, "social_intelligence": 0.4},
    "education":   {"temperature": 0.5, "confidence_baseline": 0.80,
                    "creativity": 0.5, "thoroughness": 0.7,
                    "speed": 0.4, "social_intelligence": 0.85},
    "entertainment":{"temperature": 0.75, "confidence_baseline": 0.55,
                    "creativity": 0.85, "thoroughness": 0.3,
                    "speed": 0.8, "social_intelligence": 0.9},
    "personal":    {"temperature": 0.55, "confidence_baseline": 0.70,
                    "creativity": 0.4, "thoroughness": 0.5,
                    "speed": 0.7, "social_intelligence": 0.85},
    "health":      {"temperature": 0.3, "confidence_baseline": 0.85,
                    "creativity": 0.2, "thoroughness": 0.9,
                    "speed": 0.4, "social_intelligence": 0.6},
    "home":        {"temperature": 0.45, "confidence_baseline": 0.75,
                    "creativity": 0.4, "thoroughness": 0.6,
                    "speed": 0.6, "social_intelligence": 0.7},
    "general":     {"temperature": 0.5, "confidence_baseline": 0.80,
                    "creativity": 0.3, "thoroughness": 0.6,
                    "speed": 0.5, "social_intelligence": 0.5},
    "core":        {"temperature": 0.4, "confidence_baseline": 0.85,
                    "creativity": 0.2, "thoroughness": 0.8,
                    "speed": 0.5, "social_intelligence": 0.4},
}

# Domain → list of sub-domains / specializations for targeted breeding
DOMAIN_SPECIALIZATIONS: dict[str, list[str]] = {
    "engineering": ["backend", "frontend", "devops", "data-engineering", "mlops"],
    "research":    ["literature-review", "experiment-design", "data-analysis",
                    "hypothesis-generation"],
    "security":    ["vulnerability-scanning", "policy-audit", "incident-response"],
    "finance":     ["portfolio-management", "risk-assessment", "tax-planning"],
    "creative":    ["writing", "design", "music", "video"],
    "legal":       ["contract-review", "compliance", "ip-law"],
    "education":   ["curriculum-design", "assessment", "tutoring"],
    "entertainment":["gaming", "storytelling", "trivia"],
}


# ── Gap Analyzer ────────────────────────────────────────────────────

class GapAnalyzer:
    """Detect capability gaps across the court's domain coverage.

    A gap exists when:
        - A domain has 0 active ministers (coverage gap)
        - A domain has ministers but all with low merit (quality gap)
        - A domain has low genetic diversity (diversity gap)
    """

    # Thresholds
    MIN_COVERAGE = 1        # at least 1 minister per domain
    MIN_AVG_MERIT = 35.0    # average merit below this = quality gap
    MIN_DIVERSITY = 0.20    # diversity score below this = diversity gap
    MAX_GAPS = 5            # cap gaps to avoid overwhelming breeding

    def __init__(
        self,
        min_coverage: int = MIN_COVERAGE,
        min_avg_merit: float = MIN_AVG_MERIT,
        min_diversity: float = MIN_DIVERSITY,
        max_gaps: int = MAX_GAPS,
    ) -> None:
        self.min_coverage = min_coverage
        self.min_avg_merit = min_avg_merit
        self.min_diversity = min_diversity
        self.max_gaps = max_gaps

    def analyze(
        self,
        domain_expertise: dict[str, dict[str, float]],
        merit_scores: dict[str, float],
        diversity_score: float,
    ) -> list[CapabilityGap]:
        """Scan all known domains for capability gaps.

        Args:
            domain_expertise: minister → {domain: match_score}
            merit_scores: minister → sliding_merit_score
            diversity_score: global gene diversity (0-1)

        Returns:
            Sorted list of CapabilityGap (most severe first).
        """
        gaps: list[CapabilityGap] = []

        for domain in KNOWN_DOMAINS:
            # Find ministers covering this domain
            covering = self._covering_ministers(domain_expertise, domain)

            gap = self._evaluate_domain_gap(
                domain, covering, merit_scores, diversity_score,
            )
            if gap:
                gaps.append(gap)

        # Sort by severity descending, cap at max_gaps
        gaps.sort(key=lambda g: g.severity, reverse=True)
        return gaps[:self.max_gaps]

    def _covering_ministers(
        self,
        expertise: dict[str, dict[str, float]],
        domain: str,
    ) -> dict[str, float]:
        """Return {minister: match_score} for ministers covering domain."""
        covering: dict[str, float] = {}
        for minister, domains in expertise.items():
            score = domains.get(domain, 0.0)
            if score > 0.1:  # minimum relevance threshold
                covering[minister] = score
        return covering

    def _evaluate_domain_gap(
        self,
        domain: str,
        covering: dict[str, float],
        merit_scores: dict[str, float],
        global_diversity: float,
    ) -> Optional[CapabilityGap]:
        """Evaluate a single domain for gaps.  Returns None if no gap."""
        count = len(covering)

        # Coverage gap: no ministers
        if count < self.min_coverage:
            return CapabilityGap(
                domain=domain,
                severity=self._severity_from_coverage(count),
                reason=f"零覆盖: {domain} 领域无活跃大臣",
                ministers_present=count,
                avg_merit=0.0,
                diversity_score=global_diversity,
            )

        # Quality gap: low average merit
        merits = [
            merit_scores.get(m, 20.0) for m in covering
        ]
        avg_merit = sum(merits) / len(merits)
        if avg_merit < self.min_avg_merit:
            return CapabilityGap(
                domain=domain,
                severity=self._severity_from_merit(avg_merit),
                reason=f"质量不足: {domain}领域平均功勋{avg_merit:.1f}",
                ministers_present=count,
                avg_merit=avg_merit,
                diversity_score=global_diversity,
            )

        # Diversity gap: homogeneous genomes
        if global_diversity < self.min_diversity:
            return CapabilityGap(
                domain=domain,
                severity=self._severity_from_diversity(global_diversity),
                reason=f"多样性低: 全局基因多样性{global_diversity:.3f}",
                ministers_present=count,
                avg_merit=avg_merit,
                diversity_score=global_diversity,
            )

        return None

    @staticmethod
    def _severity_from_coverage(count: int) -> float:
        """0 ministers = 1.0 severity, 1+ = 0.0."""
        if count == 0:
            return 1.0
        return 0.0

    @staticmethod
    def _severity_from_merit(avg_merit: float) -> float:
        """merit 0 → 1.0, merit 35 → 0.0."""
        return max(0.0, 1.0 - avg_merit / 35.0)

    @staticmethod
    def _severity_from_diversity(diversity: float) -> float:
        """diversity 0 → 1.0, diversity 0.20 → 0.0."""
        return max(0.0, 1.0 - diversity / 0.20)


# ── Strategy Selector ───────────────────────────────────────────────

class StrategySelector:
    """Choose the best breeding strategy for each capability gap.

    Decision logic:
        - Coverage gaps (0 ministers)     → SPECIALIST (domain expert)
        - Quality gaps (low merit)        → SPECIALIZE (clone elite + specialize)
        - Diversity gaps (homogeneous)    → EXPLORE (random high-diversity)
        - Mix of above                    → HYBRID (crossover)

    Adaptive mode: when a StrategyPerformanceTracker is attached via
    set_performance_tracker(), weights evolve based on historical outcomes.
    Strategies that produce ministers who survive/promote/thrive get higher
    selection probability.
    """

    # Default fallback weights (used when no tracker or insufficient data)
    SPECIALIST_WEIGHT = 0.35
    SPECIALIZE_WEIGHT = 0.30
    EXPLORE_WEIGHT = 0.25
    HYBRID_WEIGHT = 0.10

    # Weight blend ratio for adaptive vs. static weights
    ADAPTIVE_BLEND = 0.60  # 60% adaptive, 40% static

    def __init__(self) -> None:
        self._tracker: Optional[StrategyPerformanceTracker] = None

    def set_performance_tracker(
        self, tracker: StrategyPerformanceTracker,
    ) -> None:
        """Attach a performance tracker for adaptive weight adjustment.

        Once attached, _choose_strategy_weighted() shifts from pure static
        weights to a blend that favors historically successful strategies.
        """
        self._tracker = tracker

    def select(
        self,
        gaps: list[CapabilityGap],
        elite_ministers: list[str],
    ) -> list[BreedingCandidate]:
        """Map each gap to a breeding strategy and produce candidates.

        Args:
            gaps: Detected capability gaps.
            elite_ministers: Names of top-performing ministers (for SPECIALIZE).

        Returns:
            List of BreedingCandidate proposals.
        """
        candidates: list[BreedingCandidate] = []

        for gap in gaps:
            strategy = self._choose_strategy(gap)
            genome = self._build_genome_template(gap, strategy, elite_ministers)

            parent = None
            if strategy == BreedingStrategy.SPECIALIZE and elite_ministers:
                parent = random.choice(elite_ministers)

            candidates.append(BreedingCandidate(
                target_domain=gap.domain,
                strategy=strategy,
                genome_template=genome,
                parent_minister=parent,
                reasoning=gap.reason,
            ))

        return candidates

    def _choose_strategy(self, gap: CapabilityGap) -> BreedingStrategy:
        """Strategize based on gap characteristics and adaptive weights."""
        if gap.ministers_present == 0:
            return BreedingStrategy.SPECIALIST
        elif gap.avg_merit < 25:
            return BreedingStrategy.SPECIALIZE
        elif gap.diversity_score < 0.10:
            return BreedingStrategy.EXPLORE
        else:
            return self._choose_strategy_weighted()

    def _choose_strategy_weighted(self) -> BreedingStrategy:
        """Weighted random selection, blending static + adaptive weights."""
        if self._tracker and self._tracker.get_outcome_count() >= 5:
            # Adaptive: blend tracker composite scores with static weights
            weights = self._get_adaptive_weights()
        else:
            weights = self._get_static_weights()

        roll = random.random()
        cumulative = 0.0
        for strategy in BreedingStrategy:
            cumulative += weights.get(strategy, 0.0)
            if roll < cumulative:
                return strategy
        # Fallback: uniform
        return random.choice(list(BreedingStrategy))

    def _get_static_weights(self) -> dict[BreedingStrategy, float]:
        """Return normalized static weights."""
        raw = {
            BreedingStrategy.SPECIALIST: self.SPECIALIST_WEIGHT,
            BreedingStrategy.SPECIALIZE: self.SPECIALIZE_WEIGHT,
            BreedingStrategy.EXPLORE: self.EXPLORE_WEIGHT,
            BreedingStrategy.HYBRID: self.HYBRID_WEIGHT,
        }
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}

    def _get_adaptive_weights(self) -> dict[BreedingStrategy, float]:
        """Blend static weights with performance-tracker composite scores."""
        static = self._get_static_weights()
        scores = self._tracker.get_all_scores()

        blended: dict[BreedingStrategy, float] = {}
        for s in BreedingStrategy:
            adaptive = scores.get(s, 0.5)
            blended[s] = (
                static.get(s, 0.25) * (1.0 - self.ADAPTIVE_BLEND)
                + adaptive * self.ADAPTIVE_BLEND
            )

        # Ensure no strategy drops to zero
        min_weight = 0.05
        for s in BreedingStrategy:
            blended[s] = max(blended[s], min_weight)

        total = sum(blended.values())
        return {k: v / total for k, v in blended.items()}

    def get_current_weights(self) -> dict[BreedingStrategy, float]:
        """Return current effective selection weights (for introspection)."""
        if self._tracker and self._tracker.get_outcome_count() >= 5:
            return self._get_adaptive_weights()
        return self._get_static_weights()

    def _build_genome_template(
        self,
        gap: CapabilityGap,
        strategy: BreedingStrategy,
        elite_ministers: list[str],
    ) -> Optional[dict[str, float]]:
        """Build a target genome profile for the new minister."""
        if strategy == BreedingStrategy.SPECIALIST:
            # Use the domain's preset ideal profile
            profile = DOMAIN_PROFILES.get(gap.domain)
            if profile:
                return dict(profile)
        elif strategy == BreedingStrategy.EXPLORE:
            # Anti-profile: invert the domain profile
            base = DOMAIN_PROFILES.get(gap.domain, {})
            if base:
                return {k: 1.0 - v for k, v in base.items()}
        # SPECIALIZE and HYBRID: genome derived from parent, not preset
        return None


# ── Genome Generator ────────────────────────────────────────────────

class GenomeGenerator:
    """Generate concrete MinisterGenome instances from BreedingCandidates.

    Uses the existing genome structure (6-dimensional vector):
        temperature, confidence_baseline, creativity,
        thoroughness, speed, social_intelligence
    """

    # Mutation magnitude for SPECIALIZE strategy
    SPECIALIZE_MUTATION = 0.15

    # Mutation magnitude for EXPLORE strategy (wider exploration)
    EXPLORE_MUTATION = 0.30

    # Default genome center
    DEFAULT_GENOME = {
        "temperature": 0.50,
        "confidence_baseline": 0.80,
        "creativity": 0.30,
        "thoroughness": 0.60,
        "speed": 0.50,
        "social_intelligence": 0.50,
    }

    GENOME_KEYS = [
        "temperature", "confidence_baseline", "creativity",
        "thoroughness", "speed", "social_intelligence",
    ]

    def __init__(self) -> None:
        self._name_counter: dict[str, int] = {}

    def generate(
        self,
        candidate: BreedingCandidate,
        parent_genome: Optional[dict[str, float]] = None,
    ) -> dict[str, float]:
        """Produce a concrete genome dict from a breeding candidate.

        Args:
            candidate: The breeding plan.
            parent_genome: Optional parent genome (for SPECIALIZE/HYBRID).

        Returns:
            Dict of {gene_name: value} clamped to [0, 1].
        """
        strategy = candidate.strategy

        if strategy == BreedingStrategy.SPECIALIST:
            return self._specialist(candidate)
        elif strategy == BreedingStrategy.EXPLORE:
            return self._explore(candidate)
        elif strategy == BreedingStrategy.SPECIALIZE:
            return self._specialize(candidate, parent_genome)
        elif strategy == BreedingStrategy.HYBRID:
            return self._hybrid(parent_genome)
        else:
            return self._random_genome()

    def _specialist(self, candidate: BreedingCandidate) -> dict[str, float]:
        """Directly use the domain profile with small noise."""
        profile = (
            candidate.genome_template
            or DOMAIN_PROFILES.get(candidate.target_domain)
            or self.DEFAULT_GENOME
        )
        genome = {}
        for key in self.GENOME_KEYS:
            noise = random.uniform(-0.03, 0.03)
            genome[key] = self._clamp(profile.get(key, 0.5) + noise)
        return genome

    def _explore(self, candidate: BreedingCandidate) -> dict[str, float]:
        """Anti-profile with high variance for diversity exploration."""
        base = candidate.genome_template or {
            k: 1.0 - v for k, v in self.DEFAULT_GENOME.items()
        }
        genome = {}
        for key in self.GENOME_KEYS:
            noise = random.uniform(-self.EXPLORE_MUTATION, self.EXPLORE_MUTATION)
            genome[key] = self._clamp(base.get(key, 0.5) + noise)
        return genome

    def _specialize(
        self,
        candidate: BreedingCandidate,
        parent_genome: Optional[dict[str, float]],
    ) -> dict[str, float]:
        """Clone parent + nudge toward domain profile."""
        base = dict(parent_genome) if parent_genome else dict(self.DEFAULT_GENOME)
        profile = candidate.genome_template or DOMAIN_PROFILES.get(
            candidate.target_domain, self.DEFAULT_GENOME,
        )

        genome = {}
        for key in self.GENOME_KEYS:
            # Blend parent (70%) with domain profile (30%) + noise
            parent_val = base.get(key, 0.5)
            target_val = profile.get(key, 0.5)
            blended = parent_val * 0.7 + target_val * 0.3
            noise = random.uniform(
                -self.SPECIALIZE_MUTATION, self.SPECIALIZE_MUTATION,
            )
            genome[key] = self._clamp(blended + noise)

        return genome

    def _hybrid(
        self,
        parent_genome: Optional[dict[str, float]],
    ) -> dict[str, float]:
        """Randomly mix traits with heavy mutation."""
        base = dict(parent_genome) if parent_genome else dict(self.DEFAULT_GENOME)
        genome = {}
        for key in self.GENOME_KEYS:
            # 50% chance to keep parent value, 50% random
            if random.random() < 0.5:
                genome[key] = base.get(key, 0.5)
            else:
                genome[key] = self._clamp(
                    base.get(key, 0.5) + random.uniform(-0.4, 0.4),
                )
        return genome

    def _random_genome(self) -> dict[str, float]:
        """Fully random genome."""
        return {
            key: random.uniform(0.05, 0.95)
            for key in self.GENOME_KEYS
        }

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.01, min(0.99, value))


# ── AutoBreeder ─────────────────────────────────────────────────────

class AutoBreeder:
    """Proactive minister breeding engine.

    Sits between GapAnalyzer → StrategySelector → GenomeGenerator.

    Typical usage (inside evolution cycle):
        breeder = AutoBreeder()
        breeder.set_expertise_provider(orchestrator.get_domain_expertise)
        breeder.set_merit_provider(lambda m: survival.compute_merit(m))
        breeder.set_diversity_provider(survival.get_diversity_score)

        candidates = breeder.breed(active_ministers, elites)
        for c in candidates:
            new_genome = breeder.generate_genome(c, parent_genome)
            # Register new minister with this genome
    """

    # Minimum cycles between breeding rounds
    BREEDING_COOLDOWN = 5

    # Maximum new ministers per breeding round
    MAX_BREED_PER_CYCLE = 3

    # How many cycles to wait before checking outcomes
    OUTCOME_OBSERVATION_WINDOW = 10

    def __init__(
        self,
        gap_analyzer: Optional[GapAnalyzer] = None,
        strategy_selector: Optional[StrategySelector] = None,
        genome_generator: Optional[GenomeGenerator] = None,
        breeding_cooldown: int = BREEDING_COOLDOWN,
        max_per_cycle: int = MAX_BREED_PER_CYCLE,
    ) -> None:
        self.gap_analyzer = gap_analyzer or GapAnalyzer()
        self.strategy_selector = strategy_selector or StrategySelector()
        self.genome_generator = genome_generator or GenomeGenerator()
        self.breeding_cooldown = breeding_cooldown
        self.max_per_cycle = max_per_cycle

        # Providers — set by orchestrator
        self._expertise_provider: Optional[callable] = None
        self._merit_provider: Optional[callable] = None
        self._diversity_provider: Optional[callable] = None

        # Feedback tracker
        self.performance_tracker = StrategyPerformanceTracker()
        self._breed_cycle_registry: dict[str, int] = {}  # minister → breed_cycle_index
        self._breed_domain_registry: dict[str, str] = {}  # minister → domain
        self._breed_strategy_registry: dict[str, BreedingStrategy] = {}  # minister → strategy

        # Wire tracker to strategy selector for adaptive weights
        self.strategy_selector.set_performance_tracker(self.performance_tracker)

        # Internal state
        self._cycles_since_breed = self.breeding_cooldown
        self._total_bred = 0
        self._history: list[BreedingReport] = []

    # ── Providers ──────────────────────────────────────────────────

    def set_expertise_provider(
        self, provider: callable,
    ) -> None:
        """Set function that returns minister → {domain: match_score}."""
        self._expertise_provider = provider

    def set_merit_provider(
        self, provider: callable,
    ) -> None:
        """Set function that returns merit for a given minister name."""
        self._merit_provider = provider

    def set_diversity_provider(
        self, provider: callable,
    ) -> None:
        """Set function that returns current global diversity score."""
        self._diversity_provider = provider

    # ── Main API ───────────────────────────────────────────────────

    def breed(
        self,
        active_ministers: list[str],
        elite_ministers: Optional[list[str]] = None,
        parent_genomes: Optional[dict[str, dict[str, float]]] = None,
    ) -> BreedingReport:
        """Execute one breeding cycle.

        Args:
            active_ministers: Currently active minister names.
            elite_ministers: Top N ministers (for SPECIALIZE strategy).
            parent_genomes: minister → genome dict (for clone/mutate).

        Returns:
            BreedingReport with gaps, candidates, and created ministers.
        """
        # Check cooldown
        self._cycles_since_breed += 1
        if self._cycles_since_breed < self.breeding_cooldown:
            return BreedingReport(
                gaps_detected=[],
                candidates_proposed=[],
                candidates_created=[],
                strategies_used={},
                timestamp="",
            )

        # Gather signals
        expertise = self._gather_expertise(active_ministers)
        merit_scores = self._gather_merit(active_ministers)
        diversity = self._gather_diversity()

        # Detect gaps
        gaps = self.gap_analyzer.analyze(expertise, merit_scores, diversity)
        if not gaps:
            return BreedingReport(
                gaps_detected=[],
                candidates_proposed=[],
                candidates_created=[],
                strategies_used={},
                timestamp="",
            )

        # Select strategies
        elites = elite_ministers or active_ministers[:3]
        candidates = self.strategy_selector.select(gaps, elites)

        # Cap at max_per_cycle
        candidates = candidates[:self.max_per_cycle]

        # Generate genomes
        created: list[str] = []
        strategies_used: dict[BreedingStrategy, int] = {}
        cycle_index = self._total_bred + 0  # snapshot for registry
        for i, candidate in enumerate(candidates):
            parent_genome = None
            if candidate.parent_minister and parent_genomes:
                parent_genome = parent_genomes.get(candidate.parent_minister)

            genome = self.genome_generator.generate(candidate, parent_genome)
            name = self._generate_name(candidate.target_domain, i)
            # Store genome as candidate attribute (caller registers it)
            candidate.genome_template = genome  # override with concrete genome
            created.append(name)
            strategies_used[candidate.strategy] = (
                strategies_used.get(candidate.strategy, 0) + 1
            )

            # Register for outcome tracking
            self._breed_cycle_registry[name] = cycle_index
            self._breed_domain_registry[name] = candidate.target_domain
            self._breed_strategy_registry[name] = candidate.strategy

        self._total_bred += len(created)
        self._cycles_since_breed = 0

        report = BreedingReport(
            gaps_detected=gaps,
            candidates_proposed=candidates,
            candidates_created=created,
            strategies_used=strategies_used,
            timestamp="",
        )
        self._history.append(report)
        return report

    # ── Helpers ────────────────────────────────────────────────────

    def _gather_expertise(
        self, ministers: list[str],
    ) -> dict[str, dict[str, float]]:
        """Get domain expertise from provider, or build fallback."""
        if self._expertise_provider:
            try:
                return self._expertise_provider()
            except Exception:
                logger.debug(
                    "[Breeding] expertise_provider 回调失败，改用 namesake 兜底（已转为可观测）",
                    exc_info=True,
                )
        # Fallback: each minister covers their namesake domain
        return {
            m: {m: 1.0, "general": 0.3} for m in ministers
        }

    def _gather_merit(
        self, ministers: list[str],
    ) -> dict[str, float]:
        """Get merit scores from provider, or use defaults."""
        if self._merit_provider:
            try:
                return {m: self._merit_provider(m) for m in ministers}
            except Exception:
                logger.debug(
                    "[Breeding] merit_provider 回调失败，改用默认 50.0（已转为可观测）",
                    exc_info=True,
                )
        return {m: 50.0 for m in ministers}

    def _gather_diversity(self) -> float:
        """Get diversity score from provider, or use default."""
        if self._diversity_provider:
            try:
                score = self._diversity_provider()
                if isinstance(score, (int, float)):
                    return float(score)
            except Exception:
                logger.debug(
                    "[Breeding] diversity_provider 回调失败，改用默认 0.5（已转为可观测）",
                    exc_info=True,
                )
        return 0.5

    @staticmethod
    def _generate_name(domain: str, index: int) -> str:
        """Generate a themed minister name."""
        domain_titles = {
            "engineering": "工部技师", "research": "翰林学士",
            "security": "锦衣卫", "finance": "户部主事",
            "personal": "内务总管", "health": "太医令",
            "home": "执金吾", "general": "通政使",
            "core": "中枢侍郎", "creative": "司乐郎",
            "legal": "大理寺卿", "education": "国子监博士",
            "entertainment": "教坊司",
        }
        base = domain_titles.get(domain, "幕僚")
        if index > 0:
            return f"{base}{index + 1}"
        return base

    # ── Accessors ──────────────────────────────────────────────────

    def get_total_bred(self) -> int:
        return self._total_bred

    def get_history(self) -> list[BreedingReport]:
        return list(self._history)

    def get_last_report(self) -> Optional[BreedingReport]:
        return self._history[-1] if self._history else None

    def reset_cooldown(self) -> None:
        """Force breeding on next cycle."""
        self._cycles_since_breed = self.breeding_cooldown

    # ── Feedback Loop ──────────────────────────────────────────

    def check_outcomes(
        self,
        current_cycle: int,
        statuses: dict[str, str],          # minister → "ACTIVE"|"SHADOW"|"ELIMINATED"
        merit_scores: dict[str, float],    # minister → merit
        merit_history: Optional[dict[str, list[float]]] = None,  # minister → [merit over time]
    ) -> list[BreedingOutcome]:
        """Check bred ministers that have aged into observation window.

        For each bred minister whose breed cycle is within
        OUTCOME_OBSERVATION_WINDOW cycles of current_cycle,
        compute a BreedingOutcome and feed it to the tracker.

        Returns:
            List of BreedingOutcome records produced this cycle.
        """
        new_outcomes: list[BreedingOutcome] = []

        for minister, breed_cycle in list(self._breed_cycle_registry.items()):
            age = current_cycle - breed_cycle
            # Only assess ministers mature enough for evaluation
            if age < 3:
                continue

            # Only assess if not yet evaluated (remove from registry after)
            domain = self._breed_domain_registry.get(minister, "general")
            strategy = self._breed_strategy_registry.get(
                minister, BreedingStrategy.SPECIALIST,
            )

            status = statuses.get(minister, "ELIMINATED")
            max_merit = self._compute_peak_merit(
                minister, merit_history,
                default=merit_scores.get(minister, 0.0),
            )

            outcome = BreedingOutcome(
                minister_name=minister,
                domain=domain,
                strategy=strategy,
                survived=status in ("ACTIVE", "SHADOW"),
                promoted=(status == "ACTIVE"),
                max_merit=max_merit,
                cycles_survived=age,
                final_status=status,
            )

            self.performance_tracker.record(outcome)
            new_outcomes.append(outcome)

        # Remove tracked ministers from registry so they're not re-evaluated
        for outcome in new_outcomes:
            name = outcome.minister_name
            self._breed_cycle_registry.pop(name, None)
            self._breed_domain_registry.pop(name, None)
            self._breed_strategy_registry.pop(name, None)

        if new_outcomes:
            logger.info(
                "AutoBreeder feedback: %d outcomes recorded, "
                "tracker now has %d total",
                len(new_outcomes),
                self.performance_tracker.get_outcome_count(),
            )

        return new_outcomes

    @staticmethod
    def _compute_peak_merit(
        minister: str,
        merit_history: Optional[dict[str, list[float]]],
        default: float = 0.0,
    ) -> float:
        """Compute peak merit from history or fallback to default."""
        if merit_history and minister in merit_history:
            history = merit_history[minister]
            if history:
                return max(history)
        return default
