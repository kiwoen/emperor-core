"""
Intelligent Router (智能路由) — context-aware minister selection engine.

Core insight: picking ministers purely by task name misses the rich signal
already available. The Evolution engine knows which genomes are fit. The
Calibrator knows whose confidence is trustworthy. The Diversity Monitor
knows when certain genotypes are underrepresented.

The IntelligentRouter synthesizes these signals into a single routing
decision per task:

    1. Domain Match — how well does this minister's domain align?
    2. Genetic Fitness — raw fitness score from the evolution engine
    3. Calibration Trust — learned reliability from ConfidenceCalibrator
    4. Diversity Boost — bonus for underrepresented genotypes
    5. Workload Balance — penalty for recently overused ministers
    6. Memory Boost — bonus for ministers who succeeded on similar past tasks

The result: the best ministers for each task, with controlled exploration.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional


class RoutingStrategy(Enum):
    """How the router selects ministers."""
    BALANCED = auto()       # Equal weight on all signals (default)
    PURE_FITNESS = auto()   # Only genetic fitness
    EXPLORE = auto()        # Diversify — select less-used ministers
    EXPLOIT = auto()        # Pick the proven best
    CALIBRATED = auto()     # Weight heavily on calibration trust


@dataclass
class RoutingSignal:
    """A single signal contributed by a minister for a task."""
    minister: str
    domain_match: float         # 0-1, how well domain aligns
    genetic_fitness: float      # 0-1, normalized fitness
    calibration_trust: float    # 0-1, from ConfidenceCalibrator
    diversity_bonus: float      # 0-1, bonus for rare genotypes
    workload_penalty: float     # 0-1, penalty for recent usage
    memory_boost: float = 0.0   # 0-1, bonus from similar past successes
    composite_score: float = 0.0  # Computed


@dataclass
class RoutingPlan:
    """Result of a routing decision."""
    task: str
    domain: str
    selected_ministers: list[str]
    signal_map: dict[str, RoutingSignal]  # minister -> signal
    strategy: RoutingStrategy
    reasoning: str
    timestamp: float


class IntelligentRouter:
    """Context-aware minister selection.

    Usage:
        router = IntelligentRouter()
        router.set_fitness_provider(lambda m: evolution_engine.get_fitness(m))
        router.set_calibration_provider(lambda m: calibrator.get_bias(m))
        router.set_diversity_provider(lambda: diversity_monitor.get_diversity_score())

        plan = router.route(
            task="分析代码安全漏洞",
            domain="engineering",
            available_ministers=["chancellor", "censor", "guard"],
            count=2,
        )
        # plan.selected_ministers → ["censor", "chancellor"]
    """

    # Routing weights (configurable)
    DEFAULT_WEIGHTS = {
        "domain_match": 0.25,
        "genetic_fitness": 0.20,
        "calibration_trust": 0.18,
        "diversity_bonus": 0.07,
        "workload_penalty": -0.10,
        "memory_boost": 0.15,
    }

    MIN_DIVERSITY_THRESHOLD = 0.2    # Diversity boost below this
    MAX_DIVERSITY_BOOST = 0.15       # Cap on diversity bonus
    WORKLOAD_DECAY = 0.9             # Per-round workload decay

    def __init__(
        self,
        strategy: RoutingStrategy = RoutingStrategy.BALANCED,
    ) -> None:
        self.strategy = strategy

        # External providers (set by integration code)
        self._fitness_provider: Optional[Callable[[str], float]] = None
        self._calibration_provider: Optional[Callable[[str], float]] = None
        self._diversity_provider: Optional[Callable[[], float]] = None
        self._genotype_provider: Optional[Callable[[str], str]] = None
        self._merit_provider: Optional[Callable[[str], float]] = None
        self._memory_provider: Optional[Callable[[str], float]] = None

        # Internal state
        self._usage_counter: dict[str, int] = defaultdict(int)
        self._last_used: dict[str, float] = {}
        self._history: list[RoutingPlan] = []

        # Weights
        self.weights: dict[str, float] = dict(self.DEFAULT_WEIGHTS)

    # ------------------------------------------------------------------
    # Provider setup
    # ------------------------------------------------------------------

    def set_fitness_provider(self, provider: Callable[[str], float]) -> None:
        """Set provider for genetic fitness scores."""
        self._fitness_provider = provider

    def set_calibration_provider(self, provider: Callable[[str], float]) -> None:
        """Set provider for calibration trust. Returns bias; low bias = high trust."""
        self._calibration_provider = provider

    def set_diversity_provider(self, provider: Callable[[], float]) -> None:
        """Set provider for global diversity score."""
        self._diversity_provider = provider

    def set_genotype_provider(self, provider: Callable[[str], str]) -> None:
        """Set provider for minister genotype tags."""
        self._genotype_provider = provider

    def set_merit_provider(self, provider: Callable[[str], float]) -> None:
        """Set provider for minister merit scores."""
        self._merit_provider = provider

    def set_memory_provider(self, provider: Callable[[str], float]) -> None:
        """Set provider for memory-based boost.

        Provider receives minister name and returns a 0-1 boost
        based on how well this minister performed on similar past tasks.
        The caller should pre-bind the current domain and intent.
        """
        self._memory_provider = provider

    # ------------------------------------------------------------------
    # Main routing API
    # ------------------------------------------------------------------

    def route(
        self,
        task: str,
        domain: str,
        available_ministers: list[str],
        count: int = 2,
        strategy: Optional[RoutingStrategy] = None,
        forced_minister: Optional[str] = None,
    ) -> RoutingPlan:
        """Select the best ministers for a task.

        Args:
            task: Task description / intent
            domain: Task domain (engineering, finance, research, etc.)
            available_ministers: Ministers eligible for this task
            count: How many ministers to select
            strategy: Override routing strategy
            forced_minister: Always include this minister (e.g., domain expert)

        Returns:
            RoutingPlan with selected ministers and reasoning
        """
        import time

        effective_strategy = strategy or self.strategy

        if not available_ministers:
            return RoutingPlan(
                task=task,
                domain=domain,
                selected_ministers=[],
                signal_map={},
                strategy=effective_strategy,
                reasoning="无可用大臣",
                timestamp=time.time(),
            )

        if len(available_ministers) <= count:
            # Not enough ministers — use all available
            signals = self._compute_all_signals(
                task, domain, available_ministers, effective_strategy
            )
            return RoutingPlan(
                task=task,
                domain=domain,
                selected_ministers=list(available_ministers),
                signal_map=signals,
                strategy=effective_strategy,
                reasoning=f"可用大臣不足 {count} 人，全部征召",
                timestamp=time.time(),
            )

        # Compute signals for all candidates
        signal_map = self._compute_all_signals(
            task, domain, available_ministers, effective_strategy
        )

        # Apply forced minister
        selected: list[str] = []
        remaining = list(available_ministers)
        if forced_minister and forced_minister in remaining:
            selected.append(forced_minister)
            remaining.remove(forced_minister)

        # Select remaining ministers
        slots = count - len(selected)
        if slots > 0:
            pick = self._pick_ministers(
                signal_map, remaining, slots, effective_strategy
            )
            selected.extend(pick)

        # Update usage tracking
        for m in selected:
            self._usage_counter[m] += 1
            self._last_used[m] = time.time()

        plan = RoutingPlan(
            task=task,
            domain=domain,
            selected_ministers=selected,
            signal_map={
                m: signal_map[m]
                for m in selected
            },
            strategy=effective_strategy,
            reasoning=self._build_reasoning(selected, signal_map, effective_strategy),
            timestamp=time.time(),
        )
        self._history.append(plan)
        return plan

    def get_usage_stats(self) -> dict[str, int]:
        """Get per-minister usage counts."""
        return dict(self._usage_counter)

    def get_history(self, limit: int = 20) -> list[RoutingPlan]:
        """Get recent routing history."""
        return self._history[-limit:]

    def reset_usage(self, minister_name: Optional[str] = None) -> None:
        """Reset usage tracking for one or all ministers."""
        if minister_name:
            self._usage_counter.pop(minister_name, None)
            self._last_used.pop(minister_name, None)
        else:
            self._usage_counter.clear()
            self._last_used.clear()

    # ------------------------------------------------------------------
    # Signal computation
    # ------------------------------------------------------------------

    def _compute_all_signals(
        self,
        task: str,
        domain: str,
        ministers: list[str],
        strategy: RoutingStrategy,
    ) -> dict[str, RoutingSignal]:
        """Compute RoutingSignal for every candidate minister."""
        signals: dict[str, RoutingSignal] = {}

        diversity_score = 0.5
        if self._diversity_provider is not None:
            try:
                diversity_score = self._diversity_provider()
            except Exception:
                pass

        for minister in ministers:
            # 1. Domain match
            domain_match = self._compute_domain_match(minister, domain)

            # 2. Genetic fitness (normalized)
            genetic_fitness = self._safe_provider(
                self._fitness_provider, minister, default=0.5
            )
            # Normalize: fitness can be > 1 in some systems
            genetic_fitness = min(1.0, max(0.0, genetic_fitness))

            # 3. Calibration trust
            calibration_trust = self._compute_calibration_trust(minister)

            # 4. Diversity bonus
            diversity_bonus = self._compute_diversity_bonus(
                minister, diversity_score
            )

            # 5. Workload penalty
            workload_penalty = self._compute_workload_penalty(minister)

            # 6. Memory boost
            memory_boost = self._safe_provider(
                self._memory_provider, minister, default=0.0
            )

            # ── Compute composite based on strategy ──
            composite = self._compute_composite(
                domain_match=domain_match,
                genetic_fitness=genetic_fitness,
                calibration_trust=calibration_trust,
                diversity_bonus=diversity_bonus,
                workload_penalty=workload_penalty,
                memory_boost=memory_boost,
                strategy=strategy,
            )

            signals[minister] = RoutingSignal(
                minister=minister,
                domain_match=domain_match,
                genetic_fitness=genetic_fitness,
                calibration_trust=calibration_trust,
                diversity_bonus=diversity_bonus,
                workload_penalty=workload_penalty,
                memory_boost=memory_boost,
                composite_score=composite,
            )

        return signals

    def _compute_domain_match(self, minister: str, domain: str) -> float:
        """Compute domain alignment score for a minister.

        Based on minister class name to domain keyword mapping.
        In production, this uses the minister's domain expertise vector.
        """
        domain_map = {
            "chancellor": {"engineering": 0.85, "research": 0.80, "core": 0.90, "general": 0.70},
            "censor": {"security": 0.95, "engineering": 0.75, "core": 0.60, "general": 0.50},
            "ceremonies": {"personal": 0.90, "health": 0.80, "home": 0.85, "general": 0.70},
            "diviner": {"research": 0.90, "engineering": 0.60, "core": 0.70, "general": 0.55},
            "finance": {"finance": 0.98, "engineering": 0.55, "home": 0.60, "general": 0.50},
            "guard": {"security": 0.90, "engineering": 0.50, "core": 0.70, "general": 0.60},
            "historian": {"research": 0.95, "personal": 0.60, "core": 0.50, "general": 0.50},
            "works": {"engineering": 0.90, "home": 0.85, "creator": 0.80, "general": 0.60},
        }

        # Exact minister match
        if minister in domain_map:
            return domain_map[minister].get(domain, 0.50)

        # Partial match: check if domain appears in minister name
        minister_lower = minister.lower()
        if domain.lower() in minister_lower:
            return 0.80

        return 0.50  # Default

    def _compute_calibration_trust(self, minister: str) -> float:
        """Compute trust score from calibration bias.

        Low bias → high trust. High bias → low trust.
        Returns 0-1 where 1 = perfectly calibrated.
        """
        if self._calibration_provider is None:
            return 0.75  # Default: unknown but assume moderate trust

        bias = self._safe_provider(self._calibration_provider, minister, default=0.0)
        # Convert bias to trust: |bias|=0 → trust=1.0, |bias|=0.3 → trust=0.3
        trust = 1.0 - min(1.0, abs(bias) / 0.3)
        return max(0.1, trust)

    def _compute_diversity_bonus(self, minister: str, diversity_score: float) -> float:
        """Compute bonus for underrepresented genotypes.

        If global diversity is low, boost ministers with rare genotypes.
        If diversity is healthy, no bonus needed.
        """
        if diversity_score >= self.MIN_DIVERSITY_THRESHOLD:
            return 0.0  # Diversity is healthy

        if self._genotype_provider is None:
            return 0.0

        genotype = self._safe_str_provider(
            self._genotype_provider, minister, default="unknown"
        )

        # Calculate how often this genotype has been used recently
        total_usage = sum(self._usage_counter.values()) or 1
        genotype_usage = sum(
            count for m, count in self._usage_counter.items()
            if self._safe_str_provider(self._genotype_provider, m, default="") == genotype
        )
        genotype_share = genotype_usage / max(1, total_usage)

        # Rare genotypes (< 20% share) get a bonus
        if genotype_share < 0.20:
            return min(
                self.MAX_DIVERSITY_BOOST,
                (0.20 - genotype_share) * 0.75,
            )
        return 0.0

    def _compute_workload_penalty(self, minister: str) -> float:
        """Compute penalty for recently overused ministers.

        Ministers with above-average usage get a small penalty
        to ensure workload balance.
        """
        if not self._usage_counter:
            return 0.0

        usage = self._usage_counter.get(minister, 0)
        if usage == 0:
            return 0.0

        avg_usage = sum(self._usage_counter.values()) / max(1, len(self._usage_counter))
        avg_usage = max(1.0, avg_usage)

        # Penalty starts when usage > 1.5x average
        ratio = usage / avg_usage
        if ratio > 1.5:
            return min(0.10, (ratio - 1.5) * 0.05)
        return 0.0

    def _compute_composite(
        self,
        domain_match: float,
        genetic_fitness: float,
        calibration_trust: float,
        diversity_bonus: float,
        workload_penalty: float,
        memory_boost: float,
        strategy: RoutingStrategy,
    ) -> float:
        """Compute composite score based on strategy weights."""
        if strategy == RoutingStrategy.PURE_FITNESS:
            return genetic_fitness

        if strategy == RoutingStrategy.EXPLORE:
            # Inverse fitness: prefer less-proven ministers
            w = self.weights
            return (
                (1.0 - genetic_fitness) * 0.30
                + diversity_bonus * 0.40
                + calibration_trust * 0.30
                - workload_penalty
            )

        if strategy == RoutingStrategy.EXPLOIT:
            # Heavy on calibration and domain
            return (
                calibration_trust * 0.40
                + domain_match * 0.35
                + genetic_fitness * 0.25
                - workload_penalty
            )

        if strategy == RoutingStrategy.CALIBRATED:
            return (
                calibration_trust * 0.45
                + domain_match * 0.30
                + genetic_fitness * 0.15
                + diversity_bonus * 0.10
                - workload_penalty
            )

        # BALANCED (default)
        w = self.weights
        return (
            domain_match * w["domain_match"]
            + genetic_fitness * w["genetic_fitness"]
            + calibration_trust * w["calibration_trust"]
            + diversity_bonus * w["diversity_bonus"]
            + workload_penalty * w["workload_penalty"]
            + memory_boost * w["memory_boost"]
        )

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _pick_ministers(
        self,
        signal_map: dict[str, RoutingSignal],
        candidates: list[str],
        count: int,
        strategy: RoutingStrategy,
    ) -> list[str]:
        """Pick top-N ministers by composite score.

        For EXPLORE strategy, introduce controlled randomness:
        top 50% are selected by score, remaining 50% randomly sampled
        from the bottom half (exploration).
        """
        sorted_candidates = sorted(
            candidates,
            key=lambda m: signal_map[m].composite_score,
            reverse=True,
        )

        if strategy == RoutingStrategy.EXPLORE and len(candidates) > count + 2:
            # Pick top half by score, bottom half randomly for exploration
            top_count = max(1, count // 2)
            explore_count = count - top_count

            tops = sorted_candidates[:top_count]
            bottom_pool = sorted_candidates[top_count:]

            if explore_count > 0 and bottom_pool:
                explores = random.sample(
                    bottom_pool,
                    min(explore_count, len(bottom_pool)),
                )
                return tops + explores
            return tops

        return sorted_candidates[:count]

    def _build_reasoning(
        self,
        selected: list[str],
        signal_map: dict[str, RoutingSignal],
        strategy: RoutingStrategy,
    ) -> str:
        """Build human-readable reasoning for the routing decision."""
        if not selected:
            return "无大臣被选中"

        parts: list[str] = [f"策略: {strategy.name}"]
        for m in selected:
            sig = signal_map[m]
            parts.append(
                f"{m}: 综合={sig.composite_score:.2f} "
                f"(领域={sig.domain_match:.2f} "
                f"适应度={sig.genetic_fitness:.2f} "
                f"信任={sig.calibration_trust:.2f} "
                f"多样性={sig.diversity_bonus:.2f} "
                f"记忆={sig.memory_boost:.2f} "
                f"负载={sig.workload_penalty:.2f})"
            )
        return " | ".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_provider(
        provider: Optional[Callable],
        minister: str,
        default: float = 0.5,
    ) -> float:
        """Safely call a numeric provider, returning default on failure."""
        if provider is None:
            return default
        try:
            result = provider(minister)
            if result is None or not isinstance(result, (int, float)):
                return default
            return float(result)
        except Exception:
            return default

    @staticmethod
    def _safe_str_provider(
        provider: Optional[Callable],
        minister: str,
        default: str = "",
    ) -> str:
        """Safely call a string provider (e.g. genotype), returning default on failure."""
        if provider is None:
            return default
        try:
            result = provider(minister)
            if result is None:
                return default
            return str(result)
        except Exception:
            return default
