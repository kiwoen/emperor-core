"""
AutoEvolution + SurvivalMechanism (自进化 + 末位淘汰)

The court's autonomic self-improvement engine. Inspired by:
    - Natural selection: variation → selection → inheritance
    - Qin 军功爵位制: performance-based promotion/demotion
    - Modern genetic algorithms: mutation + crossover
    - Shadow cabinet (Westminster): opposition ready to replace

This module enables the court to:
    1. Auto-degrade underperforming ministers to shadow status
    2. Auto-promote shadow ministers who prove themselves
    3. Auto-eliminate hopeless ministers (末位淘汰)
    4. Clone top performers with mutations to fill vacancies
    5. Auto-tune minister parameters (temperature, prompt weights)
    6. Detect systemic weaknesses and spawn specialist replacements

The Emperor no longer needs to manually correct — the court evolves itself.
"""

from __future__ import annotations

import copy
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger("jarvis.court.evolution")

from jarvis.court.diversity import (  # noqa: E402
    CatastropheReport,
    DiversityMonitor,
    DiversitySnapshot,
)
from jarvis.court.sliding_merit import (  # noqa: E402
    SlidingMeritBoard,
    WindowMode,
)
from jarvis.court.merit_board import MeritBoard  # noqa: E402
from jarvis.court.breeding import (  # noqa: E402
    AutoBreeder,
    BreedingReport,
    BreedingCandidate,
    BreedingStrategy,
    BreedingOutcome,
    GapAnalyzer,
    StrategySelector,
    GenomeGenerator,
)


class MinisterStatus(Enum):
    """Lifecycle status of a minister in the evolutionary court."""
    ACTIVE = auto()         # Serving in court, receiving dispatches
    SHADOW = auto()         # Shadow cabinet — still trains but no votes
    PROBATION = auto()      # On notice — under evaluation
    ELIMINATED = auto()     # Permanently removed


class EvolutionAction(Enum):
    """Actions the evolution engine can take."""
    PROMOTE = auto()        # Shadow → Active
    DEMOTE = auto()         # Active → Shadow
    PROBATION_MARK = auto() # Active → Probation
    ELIMINATE = auto()      # Probation → Eliminated
    CLONE_MUTATE = auto()   # Clone top performer as new minister
    SPAWN_SPECIALIST = auto() # Create new minister for uncovered domain
    TUNE_PARAMS = auto()    # Adjust temperature, confidence baseline
    NO_ACTION = auto()


class CrossoverMode(Enum):
    """Available crossover (recombination) strategies for real-coded GA."""
    UNIFORM = auto()        # Random gene-by-gene selection from parents
    SBX = auto()            # Simulated Binary Crossover (Deb & Agrawal 1995)


class EliteTurnoverMode(Enum):
    """Strategy for determining how many top ministers are elite-protected.

    FIXED:    Always use the configured elitism_count.
    ADAPTIVE: Dynamically adjust elite count per cycle based on population
              diversity, merit variance, and court size. Protects more
              elites when diversity is low; accelerates turnover when
              natural selection is working well.
    """
    FIXED = auto()
    ADAPTIVE = auto()


class TaskDifficulty(Enum):
    """Task difficulty tiers driving adaptive evolution rates.

    Higher difficulty → more aggressive exploration (higher mutation,
    lower crossover η).  The orchestrator infers difficulty from
    intent complexity, domain novelty, and memory hit rate.
    """
    TRIVIAL = auto()   # 已知领域、已有记忆 → 几乎不变异
    EASY = auto()      # 常规任务 → 低变异高保守
    MODERATE = auto()  # 默认平衡
    HARD = auto()      # 复杂意图/新领域 → 高探索
    CRISIS = auto()    # 种群多样性崩溃后 → 极限探索


class EvolutionRateMode(Enum):
    """How mutation scale and crossover η are determined each cycle.

    FIXED:     Use constant MUTATION_SCALE and SBX_ETA.
    ADAPTIVE:  Compute rates from TaskDifficulty + diversity signal.
    """
    FIXED = auto()
    ADAPTIVE = auto()


@dataclass
class TaskContext:
    """Context passed from orchestrator to evolution engine per cycle.

    Used in ADAPTIVE rate mode to bias exploration/exploitation.
    """
    difficulty: TaskDifficulty = TaskDifficulty.MODERATE
    domain: str = ""
    intent: str = ""


@dataclass
class AdaptiveRateConfig:
    """Maps TaskDifficulty → (mutation_scale, sbx_eta) base values.

    These are blended with the diversity signal in ADAPTIVE mode.
    The effective rate = difficulty_base × diversity_factor.
    """
    mutation_scales: dict[TaskDifficulty, float] = field(default_factory=lambda: {
        TaskDifficulty.TRIVIAL:  0.15,
        TaskDifficulty.EASY:     0.40,
        TaskDifficulty.MODERATE: 1.00,
        TaskDifficulty.HARD:     2.20,
        TaskDifficulty.CRISIS:   4.00,
    })
    crossover_etas: dict[TaskDifficulty, float] = field(default_factory=lambda: {
        TaskDifficulty.TRIVIAL:  60.0,
        TaskDifficulty.EASY:     40.0,
        TaskDifficulty.MODERATE: 15.0,
        TaskDifficulty.HARD:     6.0,
        TaskDifficulty.CRISIS:   2.5,
    })
    # Diversity blend weight: 0 = pure task difficulty, 1 = pure diversity
    diversity_blend: float = 0.30
    # Clamp ranges
    min_mutation_scale: float = 0.05
    max_mutation_scale: float = 6.00
    min_crossover_eta: float = 2.0
    max_crossover_eta: float = 100.0


@dataclass
class EvolutionEvent:
    """A recorded evolution action for audit trail."""
    timestamp: str
    minister: str
    action: EvolutionAction
    reason: str
    previous_merit: float
    new_merit: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class MinisterGenome:
    """Evolvable parameters for a minister.

    These are the "genes" that can be mutated, crossed over,
    and selected for by the evolution engine.
    """
    name: str
    domain: str
    temperature: float = 0.7
    confidence_baseline: float = 0.85
    exploration_rate: float = 0.3     # How much to try new approaches
    conservatism: float = 0.5          # How much to stick with proven methods
    prompt_mutation_rate: float = 0.1  # Frequency of prompt evolution
    specialization_weight: float = 1.0 # Domain focus intensity
    generation: int = 0                # Which generation (0 = original)
    parent: str = ""                   # Cloned from whom


@dataclass
class EvolutionReport:
    """Summary of one evolution cycle's actions."""
    cycle: int
    actions_taken: list[EvolutionEvent]
    active_count: int
    shadow_count: int
    eliminated_count: int
    new_spawns: int
    systemic_issues: list[str]
    recommendations: list[str]


class SurvivalMechanism:
    """末位淘汰 — eliminates underperformers and fills vacancies.

    The evolutionary lifecycle:
        1. Probation candidates identified via MeritBoard
        2. 3 consecutive probation cycles → elimination
        3. Eliminated minister's genome archived for analysis
        4. Vacancy filled by crossover (双亲交叉) or clone-mutate

    Key principles:
        - Elitism: top N ministers are protected from demotion/probation
        - Crossover: combine two top performers to create superior offspring
        - Mutation: single-parent cloning with random perturbation (fallback)
        - Diversity: crossover + mutation rates adapt to population diversity

    The court has finite capacity. Underperformers must make way for
    potentially better variants — evolved through natural selection.
    """

    # How many probation cycles before elimination
    MAX_PROBATION_CYCLES = 3

    # How many dispatches per probation cycle
    DISPATCHES_PER_CYCLE = 10

    # Minimum court size
    MIN_COURT_SIZE = 4

    # Number of top ministers protected from demotion/probation
    ELITISM_COUNT = 2

    # Minimum merit required to qualify for elite protection
    ELITE_MERIT_FLOOR = 30

    # ── Adaptive Elite Turnover ─────────────────────────────────────
    # When TURNOVER_MODE is ADAPTIVE, these control the dynamic range
    # of elite count.  The system adjusts _current_elite_count each cycle
    # based on diversity, merit variance, and population size.
    TURNOVER_MODE: EliteTurnoverMode = EliteTurnoverMode.ADAPTIVE
    MIN_ELITES = 1          # Absolute floor — always protect at least 1
    MAX_ELITES = 5          # Absolute ceiling — never protect more than 5
    # ─────────────────────────────────────────────────────────────────

    # ── Adaptive Evolution Rate ─────────────────────────────────────
    # When RATE_MODE is ADAPTIVE, mutation_scale and sbx_eta are computed
    # per cycle from TaskDifficulty + diversity blend.  When FIXED, the
    # classic MUTATION_SCALE / SBX_ETA constants are used.
    RATE_MODE: EvolutionRateMode = EvolutionRateMode.ADAPTIVE
    # ─────────────────────────────────────────────────────────────────

    # ── Sliding Merit Window ────────────────────────────────────────
    # When ENABLE_SLIDING_MERIT is True and merit_board is a MeritBoard
    # (not already a SlidingMeritBoard), the SurvivalMechanism auto-wraps
    # it in SlidingMeritBoard on startup.  This makes merit evaluation
    # responsive to RECENT performance instead of lifetime averages.
    ENABLE_SLIDING_MERIT = True
    SLIDING_WINDOW_SIZE = 50       # dispatches to consider
    SLIDING_WINDOW_MODE: WindowMode = WindowMode.HARD_CUTOFF

    # ── AutoBreeder ─────────────────────────────────────────────────
    # When ENABLE_AUTO_BREEDING is True, the survival mechanism
    # proactively breeds new ministers based on capability gaps,
    # diversity deficits, and merit trends — closing the
    # 「breed → evaluate → survive」 loop.
    ENABLE_AUTO_BREEDING = True
    BREEDING_COOLDOWN = 5          # cycles between breeding rounds
    MAX_BREED_PER_CYCLE = 3        # new ministers per breeding round
    # ─────────────────────────────────────────────────────────────────

    # Probability of using crossover (vs clone-mutate) when filling vacancies
    CROSSOVER_RATE = 0.6

    # Mutation rate scaling factor (applied to genomic mutation deltas)
    MUTATION_SCALE = 1.0

    # SBX distribution index (η_c): higher → offspring closer to parents
    # Range: 2–100.  Default 15 is balanced.  Lower values → more exploration.
    SBX_ETA = 15.0

    def __init__(
        self,
        merit_board: Any = None,
        minister_registry: Optional[dict[str, Any]] = None,
        elitism_count: int = ELITISM_COUNT,
        crossover_rate: float = CROSSOVER_RATE,
        crossover_mode: CrossoverMode = CrossoverMode.SBX,
        sbx_eta: float = SBX_ETA,
        turnover_mode: EliteTurnoverMode = TURNOVER_MODE,
        min_elites: int = MIN_ELITES,
        max_elites: int = MAX_ELITES,
        rate_mode: EvolutionRateMode = RATE_MODE,
        rate_config: Optional[AdaptiveRateConfig] = None,
        enable_sliding_merit: bool = ENABLE_SLIDING_MERIT,
        sliding_window_size: int = SLIDING_WINDOW_SIZE,
        sliding_window_mode: WindowMode = SLIDING_WINDOW_MODE,
        enable_auto_breeding: bool = ENABLE_AUTO_BREEDING,
        breeding_cooldown: int = BREEDING_COOLDOWN,
        max_breed_per_cycle: int = MAX_BREED_PER_CYCLE,
        gap_analyzer: Optional[GapAnalyzer] = None,
        strategy_selector: Optional[StrategySelector] = None,
        genome_generator: Optional[GenomeGenerator] = None,
    ) -> None:
        # ── Sliding merit: auto-wrap if enabled and board is plain MeritBoard ──
        if (
            enable_sliding_merit
            and isinstance(merit_board, MeritBoard)
            and not isinstance(merit_board, SlidingMeritBoard)
        ):
            logger.info(
                "Wrapping MeritBoard in SlidingMeritBoard "
                "(window=%d, mode=%s)",
                sliding_window_size, sliding_window_mode.name,
            )
            merit_board = SlidingMeritBoard(
                merit_board,
                window_size=sliding_window_size,
                mode=sliding_window_mode,
            )

        self._merit_board = merit_board
        self._registry = minister_registry or {}
        self._statuses: dict[str, MinisterStatus] = {}
        self._probation_cycles: dict[str, int] = {}
        self._genomes: dict[str, MinisterGenome] = {}
        self._archive: list[MinisterGenome] = []  # Eliminated genomes
        self._events: list[EvolutionEvent] = []
        self._cycle_count = 0
        self._elitism_count = max(1, elitism_count)
        self._crossover_rate = max(0.0, min(1.0, crossover_rate))
        self._crossover_mode = crossover_mode
        self._sbx_eta = max(2.0, min(100.0, sbx_eta))
        self._mutation_scale = self.MUTATION_SCALE

        # Adaptive elite turnover
        self._turnover_mode = turnover_mode
        self._min_elites = max(1, min_elites)
        self._max_elites = max(self._min_elites, max_elites)
        self._current_elite_count = self._elitism_count  # updated each cycle

        # Diversity monitoring — detects population monoculture
        self.diversity = DiversityMonitor()
        self._catastrophe_cooldown_cycles = 0

        # Adaptive evolution rate
        self._rate_mode = rate_mode
        self._rate_config = rate_config or AdaptiveRateConfig()
        self._task_context = TaskContext()  # updated per cycle by orchestrator
        self._effective_mutation_scale = self._mutation_scale
        self._effective_sbx_eta = self._sbx_eta

        # ── AutoBreeder ──────────────────────────────────────────
        self._enable_auto_breeding = enable_auto_breeding
        self._auto_breeder: Optional[AutoBreeder] = None
        self._breeding_history: list[BreedingReport] = []
        if self._enable_auto_breeding:
            self._auto_breeder = AutoBreeder(
                gap_analyzer=gap_analyzer,
                strategy_selector=strategy_selector,
                genome_generator=genome_generator,
                breeding_cooldown=breeding_cooldown,
                max_per_cycle=max_breed_per_cycle,
            )
            self._wire_breeder_providers()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_minister(
        self,
        name: str,
        domain: str = "",
        temperature: float = 0.7,
        confidence_baseline: float = 0.85,
    ) -> None:
        """Register a new minister for evolutionary tracking."""
        self._statuses[name] = MinisterStatus.ACTIVE
        self._probation_cycles[name] = 0
        genome = MinisterGenome(
            name=name,
            domain=domain,
            temperature=temperature,
            confidence_baseline=confidence_baseline,
            generation=0,
        )
        self.set_genome(name, genome)

    def register_shadow(self, name: str, domain: str = "") -> None:
        """Register a shadow minister (trains but doesn't vote)."""
        self._statuses[name] = MinisterStatus.SHADOW
        self._probation_cycles[name] = 0
        self.set_genome(name, MinisterGenome(
            name=name, domain=domain, generation=0,
        ))

    # ------------------------------------------------------------------
    # Evolution cycle
    # ------------------------------------------------------------------

    def set_task_context(self, ctx: TaskContext) -> None:
        """Update the task context for the upcoming evolution cycle.

        Called by CourtOrchestrator before each dispatch to inject
        intent-level difficulty signals into the evolution engine.
        """
        self._task_context = ctx

    def _compute_adaptive_rates(self) -> None:
        """Compute effective mutation_scale and sbx_eta for this cycle.

        ADAPTIVE mode blends two signals:
            1. TaskDifficulty → base (mutation_scale, sbx_eta) from config
            2. Diversity → factor that pushes toward exploration when
               diversity is low, conservatism when high.

        FIXED mode: no-op, keeps the classic constants.
        """
        if self._rate_mode != EvolutionRateMode.ADAPTIVE:
            return

        cfg = self._rate_config
        diff = self._task_context.difficulty
        base_mut = cfg.mutation_scales.get(diff, 1.0)
        base_eta = cfg.crossover_etas.get(diff, 15.0)

        # Diversity blend: low diversity → more exploration
        try:
            d_score = self.diversity.get_latest_score()
        except Exception:
            d_score = 0.5

        # d_factor ∈ [0.3, 1.7]:  low diversity pushes factor > 1
        # (multiplies mutation, divides eta), high diversity pulls < 1
        d_factor = max(0.3, min(1.7, 1.0 + (0.3 - d_score) * 1.4))
        blend = cfg.diversity_blend
        blended = 1.0 + (d_factor - 1.0) * blend

        eff_mut = base_mut * blended
        eff_eta = base_eta / max(0.3, blended)

        self._effective_mutation_scale = max(
            cfg.min_mutation_scale,
            min(cfg.max_mutation_scale, eff_mut),
        )
        self._effective_sbx_eta = max(
            cfg.min_crossover_eta,
            min(cfg.max_crossover_eta, eff_eta),
        )

        logger.debug(
            "Adaptive rates — difficulty=%s, diversity=%.3f, "
            "eff_mut=%.3f, eff_eta=%.1f",
            diff.name, d_score,
            self._effective_mutation_scale,
            self._effective_sbx_eta,
        )

    # ------------------------------------------------------------------
    # AutoBreeder integration
    # ------------------------------------------------------------------

    def _wire_breeder_providers(self) -> None:
        """Wire AutoBreeder signal providers from current court state."""
        if not self._auto_breeder:
            return

        self._auto_breeder.set_diversity_provider(
            lambda: self.diversity.get_latest_score()
        )
        self._auto_breeder.set_merit_provider(
            lambda m: self._get_minister_merit(m)
        )

    def _get_minister_merit(self, minister: str) -> float:
        """Get merit score for a minister, with fallback."""
        try:
            if self._merit_board is not None:
                # SlidingMeritBoard uses compute_merit, MeritBoard uses get_score
                if hasattr(self._merit_board, "compute_merit"):
                    return self._merit_board.compute_merit(minister)
                return self._merit_board.get_score(minister)
        except Exception:
            pass

        return 0.0

    def set_breeder_expertise_provider(
        self, provider: callable,
    ) -> None:
        """Set domain expertise provider for the AutoBreeder.

        Called by CourtOrchestrator to inject minister→domain mapping.
        """
        if self._auto_breeder:
            self._auto_breeder.set_expertise_provider(provider)

    def _breed_ministers(
        self,
        actions: list[EvolutionEvent],
        systemic_issues: list[str],
        recommendations: list[str],
    ) -> None:
        """Step 9: Run AutoBreeder to fill capability gaps proactively.

        This closes the 「breed → evaluate → survive」 loop by
        creating new ministers with genomes optimized for detected
        domain weaknesses before they become critical.
        """
        if not self._auto_breeder:
            return

        # Collect current court state for breeding signals
        active = self.get_active_ministers()
        if not active:
            return

        elite = list(self._get_elite_set())
        parent_genomes = {
            m: self._genomes_to_dict(g)
            for m in active
            if (g := self._genomes.get(m)) is not None
        }

        # Execute breeding cycle
        report = self._auto_breeder.breed(
            active_ministers=list(active),
            elite_ministers=elite,
            parent_genomes=parent_genomes,
        )

        self._breeding_history.append(report)

        if not report.candidates_created:
            return

        # Register newly bred ministers as shadow (train before voting)
        for i, (name, candidate) in enumerate(
            zip(report.candidates_created, report.candidates_proposed)
        ):
            genome = (
                candidate.genome_template
                or self._auto_breeder.genome_generator.generate(
                    candidate,
                )
            )
            self._register_bred_minister(
                name=name,
                domain=candidate.target_domain,
                genome=genome,
                strategy=candidate.strategy,
                parent=candidate.parent_minister or "",
            )
            actions.append(EvolutionEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                minister=name,
                action=EvolutionAction.SPAWN_SPECIALIST,
                reason=(
                    f"AutoBreeder {candidate.strategy.name} for "
                    f"domain '{candidate.target_domain}'"
                ),
                previous_merit=0.0,
                new_merit=0.0,
                details={
                    "strategy": candidate.strategy.name,
                    "domain": candidate.target_domain,
                    "parent": candidate.parent_minister or "auto",
                },
            ))

        strategies_str = ", ".join(
            f"{s.name}={c}"
            for s, c in report.strategies_used.items()
        )
        gaps_str = ", ".join(
            f"{g.domain}({g.severity:.2f})"
            for g in report.gaps_detected[:3]
        )
        systemic_issues.append(
            f"AutoBreeder 填补 {len(report.candidates_created)} 个能力缺口 "
            f"[{gaps_str}]，策略: {strategies_str}"
        )
        recommendations.append(
            f"已自动育种 {len(report.candidates_created)} 名 shadow 大臣，"
            f"需等待训练后可提拔"
        )

    def _register_bred_minister(
        self,
        name: str,
        domain: str,
        genome: dict[str, float],
        strategy: BreedingStrategy,
        parent: str = "",
    ) -> None:
        """Register a minister created by AutoBreeder.

        Bred ministers enter as SHADOW — they must prove themselves
        before being promoted to ACTIVE by the normal evolution cycle.
        """
        generation = 0
        if parent and parent in self._genomes:
            generation = self._genomes[parent].generation + 1

        self._statuses[name] = MinisterStatus.SHADOW
        self._probation_cycles[name] = 0
        self.set_genome(name, MinisterGenome(
            name=name,
            domain=domain,
            temperature=genome.get("temperature", 0.5),
            confidence_baseline=genome.get("confidence_baseline", 0.80),
            exploration_rate=genome.get("exploration_rate", 0.3),
            conservatism=genome.get("conservatism", 0.5),
            specialization_weight=1.0,
            generation=generation,
            parent=parent,
        ))
        logger.info(
            "AutoBreeder registered shadow minister '%s' "
            "domain=%s strategy=%s gen=%d parent=%s",
            name, domain, strategy.name, generation,
            parent or "none",
        )

    def _track_breeding_outcomes(self) -> list[BreedingOutcome]:
        """Step 10: Check bred minister outcomes and feed back to strategy.

        After survival/promotion/elimination cycles have run, evaluate
        which bred ministers survived and feed results to the
        StrategyPerformanceTracker for adaptive weight adjustment.

        Returns:
            BreedingOutcome records for this evaluation cycle.
        """
        if not self._auto_breeder:
            return []

        # Build status map: minister → status string
        statuses: dict[str, str] = {}
        merit_scores: dict[str, float] = {}
        for name, status in self._statuses.items():
            status_str = status.name  # "ACTIVE" / "SHADOW" / "ELIMINATED" / "PROBATION"
            statuses[name] = status_str
            merit_scores[name] = self._get_minister_merit(name)

        # Build merit history from sliding merit board
        merit_history: dict[str, list[float]] = {}
        if (
            self._merit_board is not None
            and hasattr(self._merit_board, "get_history")
        ):
            for name in self._auto_breeder._breed_cycle_registry:
                try:
                    history = self._merit_board.get_history(name)
                    if history:
                        merit_history[name] = history
                except Exception:
                    pass

        outcomes = self._auto_breeder.check_outcomes(
            current_cycle=self._cycle_count,
            statuses=statuses,
            merit_scores=merit_scores,
            merit_history=merit_history if merit_history else None,
        )

        if outcomes:
            for o in outcomes:
                logger.info(
                    "Breeding feedback: %s (%s/%s) — survived=%s promoted=%s "
                    "merit=%.1f cycles=%d status=%s",
                    o.minister_name, o.domain, o.strategy.name,
                    o.survived, o.promoted,
                    o.max_merit, o.cycles_survived, o.final_status,
                )

            # Log strategy effectiveness summary
            scores = self._auto_breeder.performance_tracker.get_all_scores()
            summary = " | ".join(
                f"{s.name}: {v:.2f}" for s, v in sorted(
                    scores.items(), key=lambda x: x[1], reverse=True,
                )
            )
            logger.info("Breeding strategy effectiveness: %s", summary)

        return outcomes

    @staticmethod
    def _genomes_to_dict(genome: MinisterGenome) -> dict[str, float]:
        """Convert MinisterGenome to dict for AutoBreeder consumption."""
        return {
            "temperature": genome.temperature,
            "confidence_baseline": genome.confidence_baseline,
            "creativity": getattr(genome, "exploration_rate", 0.3),
            "thoroughness": 1.0 - getattr(genome, "exploration_rate", 0.3),
            "speed": 0.5,
            "social_intelligence": 0.5,
        }

    def run_evolution_cycle(self) -> EvolutionReport:
        """Execute one complete evolution cycle.

        Returns a report of all actions taken.
        """
        self._cycle_count += 1
        actions: list[EvolutionEvent] = []
        systemic_issues: list[str] = []
        recommendations: list[str] = []

        # Compute adaptive evolution rates before any genomic ops
        self._compute_adaptive_rates()

        if self._turnover_mode == EliteTurnoverMode.ADAPTIVE:
            self._current_elite_count = self._adaptive_elite_count()

        # Step 1: Demote critically-low-merit active ministers to shadow
        demotions = self._demote_underperformers()
        actions.extend(demotions)

        # Step 2: Identify probation candidates (merit 20-30 range)
        probations, sys_issues = self._identify_probation_candidates()
        actions.extend(probations)
        systemic_issues.extend(sys_issues)

        # Step 3: Evaluate existing probationers
        eliminations = self._process_probationers()
        actions.extend(eliminations)

        # Step 4: Promote promising shadow ministers
        promotions = self._promote_shadows()
        actions.extend(promotions)

        # Step 5: Fill vacancies
        spawns = self._fill_vacancies()
        actions.extend(spawns)

        # Step 6: Auto-tune active ministers
        tunes = self._auto_tune_ministers()
        actions.extend(tunes)

        # Step 7: Detect systemic gaps
        gaps = self._detect_systemic_gaps()
        systemic_issues.extend(gaps)

        # Counts
        active = sum(
            1 for s in self._statuses.values()
            if s == MinisterStatus.ACTIVE
        )
        shadow = sum(
            1 for s in self._statuses.values()
            if s == MinisterStatus.SHADOW
        )
        eliminated = sum(
            1 for s in self._statuses.values()
            if s == MinisterStatus.ELIMINATED
        )

        # Step 8: Measure diversity and trigger catastrophe if needed
        cat_events = self._maybe_run_catastrophe()
        actions.extend(cat_events)
        if cat_events:
            recommendations.append(
                "种群基因多样性过低，已触发大灾变重组"
            )

        # Step 9: Breed new ministers proactively (close breed→eval→survive)
        self._breed_ministers(actions, systemic_issues, recommendations)

        # Step 10: Track breeding outcomes (feedback loop)
        self._track_breeding_outcomes()

        # Recalculate spawn count after breeding may have added spawns
        spawn_count = sum(
            1 for a in actions
            if a.action in (EvolutionAction.CLONE_MUTATE, EvolutionAction.SPAWN_SPECIALIST)
        )

        return EvolutionReport(
            cycle=self._cycle_count,
            actions_taken=actions,
            active_count=active,
            shadow_count=shadow,
            eliminated_count=eliminated,
            new_spawns=spawn_count,
            systemic_issues=systemic_issues,
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------
    # Step-by-step logic
    # ------------------------------------------------------------------

    def _identify_probation_candidates(
        self,
    ) -> tuple[list[EvolutionEvent], list[str]]:
        """Identify active ministers who should enter probation.

        Elite ministers (top ELITISM_COUNT) are immune to probation.
        """
        actions: list[EvolutionEvent] = []
        issues: list[str] = []

        if self._merit_board is None:
            return actions, issues

        elites = self._get_elite_set()
        candidates = self._merit_board.get_probation_candidates()
        for minister in candidates:
            current_status = self._statuses.get(minister, MinisterStatus.ACTIVE)
            # Only active ministers can enter probation
            if current_status != MinisterStatus.ACTIVE:
                continue

            # Elitism: top performers are immune to probation
            if minister in elites:
                continue

            merit = self._merit_board.compute_merit(minister)
            self._statuses[minister] = MinisterStatus.PROBATION
            self._probation_cycles[minister] = 0
            event = EvolutionEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                minister=minister,
                action=EvolutionAction.PROBATION_MARK,
                reason=f"功勋过低({merit:.1f})，进入考核期",
                previous_merit=merit,
            )
            actions.append(event)
            self._events.append(event)
            logger.warning(
                "[Evolution] %s entered probation (merit=%.1f, cycle=%d/%d)",
                minister, merit,
                self._probation_cycles[minister],
                self.MAX_PROBATION_CYCLES,
            )
            issues.append(f"{minister} 功勋 {merit:.1f} 进入考核期")

        return actions, issues

    def _process_probationers(self) -> list[EvolutionEvent]:
        """Evaluate ministers currently in probation.

        Increments probation cycle each evaluation.
        If probation_cycles >= MAX → eliminate (elites excluded).
        If merit has recovered → remove probation.

        Elite ministers (top ELITISM_COUNT) are immune to elimination.
        """
        actions: list[EvolutionEvent] = []
        elites = self._get_elite_set()

        for minister, status in list(self._statuses.items()):
            if status != MinisterStatus.PROBATION:
                continue

            # Increment cycle on each evaluation
            self._probation_cycles[minister] = (
                self._probation_cycles.get(minister, 0) + 1
            )
            cycles = self._probation_cycles[minister]

            merit = (
                self._merit_board.compute_merit(minister)
                if self._merit_board else 0
            )

            if cycles >= self.MAX_PROBATION_CYCLES:
                # Elitism: protect top performers from elimination
                if minister in elites:
                    self._statuses[minister] = MinisterStatus.ACTIVE
                    self._probation_cycles[minister] = 0
                    logger.info(
                        "[Evolution] %s spared by elitism despite probation",
                        minister,
                    )
                    continue

                # Too many cycles — eliminate
                if self._can_eliminate():
                    self._statuses[minister] = MinisterStatus.ELIMINATED
                    self._archive_genome(minister)
                    if self._merit_board:
                        self._merit_board.mark_eliminated(minister)
                    event = EvolutionEvent(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        minister=minister,
                        action=EvolutionAction.ELIMINATE,
                        reason=f"连续{cycles}轮考核未通过，末位淘汰",
                        previous_merit=merit,
                    )
                    actions.append(event)
                    self._events.append(event)
                    logger.warning(
                        "[Evolution] %s ELIMINATED after %d probation cycles",
                        minister, cycles,
                    )
            elif merit > 40:
                # Recovered — remove probation
                self._statuses[minister] = MinisterStatus.ACTIVE
                self._probation_cycles[minister] = 0
                event = EvolutionEvent(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    minister=minister,
                    action=EvolutionAction.PROMOTE,
                    reason=f"功勋恢复至 {merit:.1f}，解除考核",
                    previous_merit=merit,
                    new_merit=merit,
                )
                actions.append(event)
                self._events.append(event)
                logger.info("[Evolution] %s recovered from probation", minister)

        return actions

    def _demote_underperformers(self) -> list[EvolutionEvent]:
        """Demote active ministers with critically low merit to shadow.

        Elite ministers (top ELITISM_COUNT) are immune to demotion.
        """
        actions: list[EvolutionEvent] = []
        elites = self._get_elite_set()

        for minister, status in list(self._statuses.items()):
            if status != MinisterStatus.ACTIVE:
                continue
            if minister in elites:
                continue  # Elite protection

            merit = (
                self._merit_board.compute_merit(minister)
                if self._merit_board else 50
            )
            # Very low merit → shadow
            if merit < 20:
                active_count = sum(
                    1 for s in self._statuses.values()
                    if s == MinisterStatus.ACTIVE
                )
                if active_count > self.MIN_COURT_SIZE:
                    self._statuses[minister] = MinisterStatus.SHADOW
                    event = EvolutionEvent(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        minister=minister,
                        action=EvolutionAction.DEMOTE,
                        reason=f"功勋过低({merit:.1f})，降为影阁",
                        previous_merit=merit,
                    )
                    actions.append(event)
                    self._events.append(event)
                    logger.warning("[Evolution] %s demoted to shadow", minister)
        return actions

    def _promote_shadows(self) -> list[EvolutionEvent]:
        """Promote shadow ministers who have proven themselves."""
        actions: list[EvolutionEvent] = []
        for minister, status in list(self._statuses.items()):
            if status != MinisterStatus.SHADOW:
                continue
            merit = (
                self._merit_board.compute_merit(minister)
                if self._merit_board else 0
            )
            if merit > 50:
                self._statuses[minister] = MinisterStatus.ACTIVE
                event = EvolutionEvent(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    minister=minister,
                    action=EvolutionAction.PROMOTE,
                    reason=f"影阁功勋达到 {merit:.1f}，升为正臣",
                    previous_merit=merit,
                    new_merit=merit,
                )
                actions.append(event)
                self._events.append(event)
                logger.info("[Evolution] %s promoted from shadow", minister)
        return actions

    def _fill_vacancies(self) -> list[EvolutionEvent]:
        """Fill court vacancies via crossover or clone-mutate.

        Strategy (in order of preference):
        1. CROSSOVER: combine two top performers from different domains (60% chance)
        2. CLONE_MUTATE: clone single top performer with mutation (fallback)

        Crossover preferred because it produces higher genetic diversity.
        """
        actions: list[EvolutionEvent] = []
        active = sum(
            1 for s in self._statuses.values()
            if s == MinisterStatus.ACTIVE
        )
        shadow = sum(
            1 for s in self._statuses.values()
            if s == MinisterStatus.SHADOW
        )
        total_available = active + shadow

        # Ideal court size: original 8 + up to 4 shadow
        if total_available >= 8:
            return actions

        # Find top N performers
        top_n = self._find_top_n(3)
        if not top_n:
            return actions

        # Try crossover if we have at least 2 candidates and RNG says yes
        if len(top_n) >= 2 and random.random() < self._crossover_rate:
            parent1 = self._genomes.get(top_n[0][0])
            parent2 = self._genomes.get(top_n[1][0])
            if parent1 and parent2 and parent1.domain != parent2.domain:
                new_name = self._generate_clone_name(parent1.name)
                crossed = self._crossover_genome(parent1, parent2, new_name)
                self.set_genome(new_name, crossed)
                self._statuses[new_name] = MinisterStatus.SHADOW
                self._probation_cycles[new_name] = 0

                event = EvolutionEvent(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    minister=new_name,
                    action=EvolutionAction.CLONE_MUTATE,
                    reason=f"交叉繁殖：{parent1.name}({parent1.domain}) × {parent2.name}({parent2.domain})",
                    previous_merit=0,
                    details={
                        "method": "crossover",
                        "parent1": parent1.name,
                        "parent2": parent2.name,
                        "domain": crossed.domain,
                        "temperature": crossed.temperature,
                        "confidence_baseline": crossed.confidence_baseline,
                        "generation": crossed.generation,
                    },
                )
                actions.append(event)
                self._events.append(event)
                logger.info(
                    "[Evolution] Crossover: %s × %s → %s (domain=%s, gen %d)",
                    parent1.name, parent2.name, new_name,
                    crossed.domain, crossed.generation,
                )
                return actions

        # Fallback: clone-mutate from single top performer
        top_name, top_merit = top_n[0]
        parent_genome = self._genomes.get(top_name)
        if parent_genome is None:
            return actions

        new_name = self._generate_clone_name(top_name)
        mutated = self._mutate_genome(parent_genome, new_name)
        self.set_genome(new_name, mutated)
        self._statuses[new_name] = MinisterStatus.SHADOW
        self._probation_cycles[new_name] = 0

        event = EvolutionEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            minister=new_name,
            action=EvolutionAction.CLONE_MUTATE,
            reason=f"克隆自 {top_name}（功勋 {top_merit:.1f}）填补空缺",
            previous_merit=0,
            details={
                "method": "clone_mutate",
                "parent": top_name,
                "mutation": {
                    "temperature": mutated.temperature,
                    "confidence_baseline": mutated.confidence_baseline,
                    "generation": mutated.generation,
                },
            },
        )
        actions.append(event)
        self._events.append(event)
        logger.info(
            "[Evolution] Cloned %s → %s (gen %d)",
            top_name, new_name, mutated.generation,
        )

        return actions

    def _auto_tune_ministers(self) -> list[EvolutionEvent]:
        """Auto-tune minister parameters based on recent performance."""
        actions: list[EvolutionEvent] = []
        for minister, status in self._statuses.items():
            if status == MinisterStatus.ELIMINATED:
                continue
            genome = self._genomes.get(minister)
            if genome is None:
                continue

            merit = (
                self._merit_board.compute_merit(minister)
                if self._merit_board else 50.0
            )

            changes: dict[str, float] = {}

            # Temperature tuning: lower temp when performing well (be more precise)
            # Higher temp when struggling (explore more)
            if merit > 70 and genome.temperature > 0.4:
                changes["temperature"] = max(0.3, genome.temperature - 0.05)
            elif merit < 30 and genome.temperature < 0.9:
                changes["temperature"] = min(1.0, genome.temperature + 0.05)

            # Confidence baseline: track long-term average
            if self._merit_board:
                entries = self._merit_board._ledger.get(minister, [])
                if entries:
                    avg_conf = sum(e.confidence for e in entries) / len(entries)
                    target_baseline = (genome.confidence_baseline + avg_conf) / 2
                    if abs(target_baseline - genome.confidence_baseline) > 0.05:
                        changes["confidence_baseline"] = target_baseline

            if changes:
                for key, val in changes.items():
                    setattr(genome, key, val)

                event = EvolutionEvent(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    minister=minister,
                    action=EvolutionAction.TUNE_PARAMS,
                    reason=f"自适应调参：{', '.join(f'{k}={v:.2f}' for k, v in changes.items())}",
                    previous_merit=merit,
                    details=changes,
                )
                actions.append(event)
                self._events.append(event)
                logger.debug("[Evolution] %s auto-tuned: %s", minister, changes)

        return actions

    def _detect_systemic_gaps(self) -> list[str]:
        """Detect systemic weaknesses in the court.

        Returns list of gap descriptions. In production, this would analyze
        dispatch patterns to find uncovered domains.
        """
        gaps: list[str] = []

        # Check court size
        active = sum(
            1 for s in self._statuses.values()
            if s == MinisterStatus.ACTIVE
        )
        if active < self.MIN_COURT_SIZE:
            gaps.append(f"朝臣不足（仅{active}人，最低{self.MIN_COURT_SIZE}）")

        # Check for domain gaps (if registry has domains)
        domains_covered: set[str] = set()
        for name, genome in self._genomes.items():
            if self._statuses.get(name) == MinisterStatus.ACTIVE:
                domains_covered.add(genome.domain)

        # Known domains that should be covered
        expected_domains = {
            "writing", "code", "research", "search", "multimodal",
            "finance", "science", "security",
        }
        missing = expected_domains - domains_covered
        if missing:
            gaps.append(f"领域缺失：{', '.join(sorted(missing))}")

        # Check for generation stagnation
        max_gen = max(
            (g.generation for g in self._genomes.values()),
            default=0,
        )
        if max_gen > 5:
            gaps.append(f"已进化至第{max_gen}代，需审查原始大臣是否过于陈旧")

        return gaps

    # ------------------------------------------------------------------
    # Diversity + Catastrophe
    # ------------------------------------------------------------------

    def _maybe_run_catastrophe(self) -> list[EvolutionEvent]:
        """Measure population diversity and trigger catastrophe if needed.

        Catastrophe fires when:
          - Diversity score stays below DIVERSITY_CRISIS_THRESHOLD for
            CRISIS_STREAK_LIMIT consecutive cycles.
          - Cool-down period has elapsed since last catastrophe.

        The catastrophe:
          1. Keeps top 3 ministers by merit
          2. Eliminates everyone else (archiving genomes)
          3. High-mutation clones survivors (3× normal scale)
          4. Injects 2 domain specialists for uncovered areas
        """
        active_names = self.get_active_ministers()
        if len(active_names) < 3:
            return []

        # Build merit scores
        merit_scores: dict[str, float] = {}
        for name in self._statuses:
            if self._statuses[name] != MinisterStatus.ELIMINATED:
                merit_scores[name] = (
                    self._merit_board.compute_merit(name)
                    if self._merit_board else 30.0
                )

        # Measure
        self.diversity.measure(self._genomes, merit_scores, active_names)

        # Check
        if not self.diversity.is_catastrophe_needed(self._cycle_count):
            return []

        # Plan
        all_names = list(self._statuses.keys())
        plan = self.diversity.plan_catastrophe(
            self._genomes, merit_scores, active_names, all_names,
        )

        return self._execute_catastrophe(plan)

    def _execute_catastrophe(self, plan: CatastropheReport) -> list[EvolutionEvent]:
        """Execute a planned catastrophe mutation.

        Re-seeds the population with high-mutation survivors and fresh
        specialists to break out of genetic monoculture.
        """
        actions: list[EvolutionEvent] = []

        # 1. Eliminate non-survivors
        eliminated = plan.details.get("eliminated", [])
        for name in eliminated:
            if self._statuses.get(name) != MinisterStatus.ELIMINATED:
                self._statuses[name] = MinisterStatus.ELIMINATED
                self._archive_genome(name)
                if self._merit_board:
                    self._merit_board.mark_eliminated(name)
                actions.append(EvolutionEvent(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    minister=name,
                    action=EvolutionAction.ELIMINATE,
                    reason="大灾变淘汰—种群基因多样性枯竭",
                    previous_merit=0,
                ))

        # 2. High-mutation clone survivors
        old_mutation_scale = self._mutation_scale
        self._mutation_scale = 3.0  # triple mutation for re-diversification
        try:
            survivor_genomes = [
                self._genomes[s] for s in plan.survivors
                if s in self._genomes
            ]
            for clone_name in plan.details.get("clones", []):
                if not survivor_genomes:
                    break
                parent = random.choice(survivor_genomes)
                mutated = self._mutate_genome(parent, clone_name)
                self.set_genome(clone_name, mutated)
                self._statuses[clone_name] = MinisterStatus.SHADOW
                self._probation_cycles[clone_name] = 0
                actions.append(EvolutionEvent(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    minister=clone_name,
                    action=EvolutionAction.CLONE_MUTATE,
                    reason=f"大灾变高突变克隆—亲本 {parent.name}（3×变异率）",
                    previous_merit=0,
                    details={
                        "method": "catastrophe_clone",
                        "parent": parent.name,
                        "mutation_scale": 3.0,
                        "temperature": mutated.temperature,
                        "generation": mutated.generation,
                    },
                ))
        finally:
            self._mutation_scale = old_mutation_scale

        # 3. Spawn domain specialists
        for spec_name in plan.details.get("specialists", []):
            # Extract domain hint from name like "新晋_security"
            parts = spec_name.rsplit("_", 1)
            domain = parts[1] if len(parts) > 1 else "general"
            # Randomize initial genome for maximum diversity
            specialist_genome = MinisterGenome(
                name=spec_name,
                domain=domain,
                temperature=random.uniform(0.4, 0.9),
                confidence_baseline=random.uniform(0.5, 0.9),
                exploration_rate=random.uniform(0.2, 0.7),
                conservatism=random.uniform(0.2, 0.6),
                prompt_mutation_rate=random.uniform(0.05, 0.3),
                specialization_weight=random.uniform(0.8, 1.5),
                generation=1,
                parent="catastrophe",
            )
            self.set_genome(spec_name, specialist_genome)
            self._statuses[spec_name] = MinisterStatus.ACTIVE
            self._probation_cycles[spec_name] = 0
            actions.append(EvolutionEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                minister=spec_name,
                action=EvolutionAction.SPAWN_SPECIALIST,
                reason=f"大灾变领域注入—填补 {domain} 领域空白",
                previous_merit=0,
                details={
                    "method": "catastrophe_specialist",
                    "domain": domain,
                    "temperature": specialist_genome.temperature,
                },
            ))

        # Log summary
        logger.critical(
            "[Evolution] Catastrophe complete: "
            "eliminated=%d, cloned=%d, specialists=%d",
            len(eliminated),
            len(plan.details.get("clones", [])),
            len(plan.details.get("specialists", [])),
        )

        return actions

    def get_diversity_score(self) -> float:
        """Get the most recent population diversity score (0–1)."""
        return self.diversity.get_latest_score()

    def get_diversity_history(self) -> list[DiversitySnapshot]:
        """Return diversity measurement history."""
        return list(self.diversity.history)

    def get_catastrophe_history(self) -> list[CatastropheReport]:
        """Return catastrophe event history."""
        return list(self.diversity.catastrophes)

    def get_auto_breeder(self) -> Optional[AutoBreeder]:
        """Access the AutoBreeder instance (None if disabled)."""
        return self._auto_breeder

    def get_breeding_history(self) -> list[BreedingReport]:
        """Return all breeding reports since court creation."""
        return list(self._breeding_history)

    def is_breeding_enabled(self) -> bool:
        """Check if auto-breeding is active."""
        return self._enable_auto_breeding and self._auto_breeder is not None

    # ------------------------------------------------------------------
    # Genome operations
    # ------------------------------------------------------------------

    def _crossover_genome(
        self,
        parent1: MinisterGenome,
        parent2: MinisterGenome,
        child_name: str,
    ) -> MinisterGenome:
        """Create offspring via crossover of two parents.

        Supports two modes:
          - UNIFORM: Random gene-by-gene selection from either parent (classic).
          - SBX: Simulated Binary Crossover — industry-standard for real-coded
            GAs. Uses spread factor β drawn from a polynomial distribution
            controlled by η_c, producing offspring near parents with high
            probability while still allowing distant exploration.

        SBX details:
            For each gene i, with parents p1(i), p2(i):
              u ~ U(0, 1)
              if u ≤ 0.5:  β = (2u)^(1/(η_c+1))
              else:         β = (1/(2(1-u)))^(1/(η_c+1))
              child(i) = 0.5[(1±β)·p1(i) + (1∓β)·p2(i)]   (random sign)
            Values clamped to [gene_min, gene_max].
        """
        # Determine which parent has higher merit
        merit1 = 0.0
        merit2 = 0.0
        if self._merit_board:
            merit1 = self._merit_board.compute_merit(parent1.name)
            merit2 = self._merit_board.compute_merit(parent2.name)

        better_parent = parent1 if merit1 >= merit2 else parent2

        if self._crossover_mode == CrossoverMode.SBX:
            return self._sbx_crossover(parent1, parent2, child_name, better_parent)
        else:
            return self._uniform_crossover(parent1, parent2, child_name, better_parent)

    def _uniform_crossover(
        self,
        parent1: MinisterGenome,
        parent2: MinisterGenome,
        child_name: str,
        better_parent: MinisterGenome,
    ) -> MinisterGenome:
        """Uniform crossover: each gene randomly picked from either parent."""
        temperature = random.choice([parent1.temperature, parent2.temperature])
        confidence_baseline = random.choice(
            [parent1.confidence_baseline, parent2.confidence_baseline]
        )
        exploration_rate = random.choice(
            [parent1.exploration_rate, parent2.exploration_rate]
        )
        conservatism = random.choice(
            [parent1.conservatism, parent2.conservatism]
        )
        prompt_mutation_rate = random.choice(
            [parent1.prompt_mutation_rate, parent2.prompt_mutation_rate]
        )
        specialization_weight = random.choice(
            [parent1.specialization_weight, parent2.specialization_weight]
        )

        domain = better_parent.domain

        temp_jitter = random.uniform(-0.05, 0.05) * self._effective_mutation_scale
        conf_jitter = random.uniform(-0.03, 0.03) * self._effective_mutation_scale

        return MinisterGenome(
            name=child_name,
            domain=domain,
            temperature=max(0.2, min(1.0, temperature + temp_jitter)),
            confidence_baseline=max(
                0.3, min(0.95, confidence_baseline + conf_jitter)
            ),
            exploration_rate=exploration_rate,
            conservatism=conservatism,
            prompt_mutation_rate=prompt_mutation_rate,
            specialization_weight=specialization_weight,
            generation=max(parent1.generation, parent2.generation) + 1,
            parent=f"{parent1.name}×{parent2.name}",
        )

    def _sbx_crossover(
        self,
        parent1: MinisterGenome,
        parent2: MinisterGenome,
        child_name: str,
        better_parent: MinisterGenome,
    ) -> MinisterGenome:
        """Simulated Binary Crossover (Deb & Agrawal, 1995).

        Each gene recombined independently via polynomial spread factor.
        Produces offspring that is a smooth blend between parents,
        with higher η_c → offspring closer to parents.
        """
        eta = self._effective_sbx_eta

        def _sbx_gene(v1: float, v2: float, lo: float, hi: float) -> float:
            """Apply SBX to a single gene value."""
            if abs(v1 - v2) < 1e-9:
                return v1  # No variation to exploit

            # Ensure consistent ordering
            if v1 > v2:
                v1, v2 = v2, v1

            u = random.random()
            if u <= 0.5:
                beta = (2.0 * u) ** (1.0 / (eta + 1.0))
            else:
                beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))

            # Random sign: child gets either spread-out or contracted value
            if random.random() < 0.5:
                child_val = 0.5 * ((1.0 + beta) * v1 + (1.0 - beta) * v2)
            else:
                child_val = 0.5 * ((1.0 - beta) * v1 + (1.0 + beta) * v2)

            return max(lo, min(hi, child_val))

        temperature = _sbx_gene(
            parent1.temperature, parent2.temperature, 0.2, 1.0,
        )
        confidence_baseline = _sbx_gene(
            parent1.confidence_baseline, parent2.confidence_baseline, 0.3, 0.95,
        )
        exploration_rate = _sbx_gene(
            parent1.exploration_rate, parent2.exploration_rate, 0.0, 1.0,
        )
        conservatism = _sbx_gene(
            parent1.conservatism, parent2.conservatism, 0.0, 1.0,
        )
        prompt_mutation_rate = _sbx_gene(
            parent1.prompt_mutation_rate, parent2.prompt_mutation_rate, 0.0, 0.5,
        )
        specialization_weight = _sbx_gene(
            parent1.specialization_weight, parent2.specialization_weight, 0.3, 2.0,
        )

        domain = better_parent.domain

        return MinisterGenome(
            name=child_name,
            domain=domain,
            temperature=temperature,
            confidence_baseline=confidence_baseline,
            exploration_rate=exploration_rate,
            conservatism=conservatism,
            prompt_mutation_rate=prompt_mutation_rate,
            specialization_weight=specialization_weight,
            generation=max(parent1.generation, parent2.generation) + 1,
            parent=f"{parent1.name}×{parent2.name}",
        )

    def _mutate_genome(
        self, parent: MinisterGenome, new_name: str
    ) -> MinisterGenome:
        """Create a mutated clone of a parent genome.

        Mutation rules:
        - Temperature: perturb by ±0.15 × mutation_scale (capped at [0.2, 1.0])
        - Confidence baseline: perturb by ±0.08 × mutation_scale (capped at [0.3, 0.95])
        - Exploration rate: flip with 30% chance
        - Prompt mutation rate: slight increase each generation
        """
        scale = self._effective_mutation_scale
        temp_delta = random.uniform(-0.15, 0.15) * scale
        conf_delta = random.uniform(-0.08, 0.08) * scale

        new_temp = max(0.2, min(1.0, parent.temperature + temp_delta))
        new_conf = max(0.3, min(0.95, parent.confidence_baseline + conf_delta))

        # Exploration: 30% chance to switch strategy
        new_explore = parent.exploration_rate
        if random.random() < 0.3:
            new_explore = 1.0 - parent.exploration_rate

        # Prompt mutation rate increases slightly each generation
        new_prompt_rate = min(0.5, parent.prompt_mutation_rate + 0.02)

        return MinisterGenome(
            name=new_name,
            domain=parent.domain,
            temperature=new_temp,
            confidence_baseline=new_conf,
            exploration_rate=new_explore,
            conservatism=parent.conservatism,
            prompt_mutation_rate=new_prompt_rate,
            specialization_weight=parent.specialization_weight,
            generation=parent.generation + 1,
            parent=parent.name,
        )

    def _generate_clone_name(self, parent_name: str) -> str:
        """Generate a clone's name based on the parent.

        Scans existing clones and picks the next available number.
        Pattern: 丞相_v2, 太卜_v3, etc.
        Crossover offspring use parent1's naming convention.
        """
        existing = [
            n for n in self._genomes
            if n.startswith(f"{parent_name}_v")
        ]
        # Also scan archive
        existing.extend(
            g.name for g in self._archive
            if g.name.startswith(f"{parent_name}_v")
        )
        if not existing:
            return f"{parent_name}_v1"
        max_num = 0
        for name in existing:
            try:
                num = int(name.split("_v")[-1])
                if num > max_num:
                    max_num = num
            except (ValueError, IndexError):
                pass
        return f"{parent_name}_v{max_num + 1}"

    def _archive_genome(self, minister: str) -> None:
        """Archive an eliminated minister's genome for analysis."""
        genome = self._genomes.get(minister)
        if genome:
            self._archive.append(copy.deepcopy(genome))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_elite_set(self) -> set[str]:
        """Return the set of elite (protected) minister names.

        Elites are the top elite_count ministers by merit,
        but only those with merit >= ELITE_MERIT_FLOOR.
        In ADAPTIVE mode, elite_count is recomputed each cycle
        based on diversity, merit variance, and population size.
        """
        threshold = max(0, self.ELITE_MERIT_FLOOR)
        elite_count = self._current_elite_count
        top_n = self._find_top_n(elite_count)
        return {
            name for name, merit in top_n
            if merit >= threshold
        }

    def _adaptive_elite_count(self) -> int:
        """Compute the dynamic elite count for this evolution cycle.

        Balances three signals:
            1. Diversity  — low diversity → protect more elites (stability)
            2. Merit σ    — low variance  → more elites (no clear leader)
            3. Court size — large court   → more slots to protect

        Returns an integer clamped to [MIN_ELITES, MAX_ELITES].
        """
        base = self._elitism_count

        # ── Signal 1: Diversity ──────────────────────────────────────
        try:
            d_score = self.diversity.get_latest_score()
        except Exception:
            d_score = 0.5
        # When diversity is low (<0.3), we want MORE elite protection.
        # When diversity is high (>0.7), natural selection works → fewer.
        diversity_factor = max(0.3, 1.0 - d_score) / 0.7

        # ── Signal 2: Merit variance ─────────────────────────────────
        actives = [
            self._merit_board.compute_merit(name)
            for name, s in self._statuses.items()
            if s == MinisterStatus.ACTIVE and self._merit_board
        ]
        if len(actives) >= 2:
            mean = sum(actives) / len(actives)
            var = sum((m - mean) ** 2 for m in actives) / len(actives)
            # Normalize: var ~ 0..2500 for merits roughly 0..100
            norm_var = min(1.0, var / 1000.0)
        else:
            norm_var = 0.5
        # High variance → clear leaders → fewer elites needed.
        # Low variance  → everyone similar → protect larger elite pool.
        variance_factor = max(0.3, 1.0 - norm_var) / 0.7

        # ── Signal 3: Court size ─────────────────────────────────────
        active_count = sum(
            1 for s in self._statuses.values() if s == MinisterStatus.ACTIVE
        )
        # Scale linearly: size 4 → factor 0.5, size 12 → factor 1.0
        size_factor = max(0.4, min(1.0, active_count / 12.0))

        # ── Composite score ──────────────────────────────────────────
        # Weighted equally; tune via class constants if needed.
        composite = (diversity_factor + variance_factor + size_factor) / 3.0
        adaptive = round(base * composite)

        return max(self._min_elites, min(self._max_elites, adaptive))

    def _can_eliminate(self) -> bool:
        """Check if we can eliminate without going below minimum court size."""
        active_and_shadow = sum(
            1 for s in self._statuses.values()
            if s in (MinisterStatus.ACTIVE, MinisterStatus.SHADOW)
        )
        return active_and_shadow > self.MIN_COURT_SIZE

    def _find_top_n(self, n: int) -> list[tuple[str, float]]:
        """Find the top N performers by merit, sorted descending.

        Returns list of (name, merit) tuples. Excludes eliminated ministers.
        """
        scored: list[tuple[str, float]] = []
        for name, status in self._statuses.items():
            if status == MinisterStatus.ELIMINATED:
                continue
            merit = (
                self._merit_board.compute_merit(name)
                if self._merit_board else 0
            )
            scored.append((name, merit))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n]

    def _find_top_performer(self) -> tuple[Optional[str], float]:
        """Find the single highest-merit minister."""
        top_n = self._find_top_n(1)
        if top_n:
            return top_n[0]
        return None, 0.0

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_status(self, minister: str) -> MinisterStatus:
        """Get the evolutionary status of a minister."""
        return self._statuses.get(minister, MinisterStatus.ACTIVE)

    def get_genome(self, minister: str) -> Optional[MinisterGenome]:
        """Get the evolvable genome of a minister."""
        return self._genomes.get(minister)

    def set_genome(self, name: str, genome: MinisterGenome) -> None:
        """Replace a minister's genome (e.g. after mutation/breeding).

        Unlike internal _genomes[name] = ..., this is the public API for
        genome replacement. Use this whenever an external actor (Emperor,
        tests, breeding loop) needs to update a genome.
        """
        self._genomes[name] = genome

    def get_active_ministers(self) -> list[str]:
        """Return list of currently active ministers."""
        return [
            name for name, s in self._statuses.items()
            if s == MinisterStatus.ACTIVE
        ]

    def get_shadow_ministers(self) -> list[str]:
        """Return list of shadow cabinet ministers."""
        return [
            name for name, s in self._statuses.items()
            if s == MinisterStatus.SHADOW
        ]

    def get_eliminated_ministers(self) -> list[str]:
        """Return list of eliminated ministers."""
        return [
            name for name, s in self._statuses.items()
            if s == MinisterStatus.ELIMINATED
        ]

    def get_evolution_history(self) -> list[EvolutionEvent]:
        """Return all evolution events for audit trail."""
        return list(self._events)

    def get_archive(self) -> list[MinisterGenome]:
        """Return archived (eliminated) genomes for analysis."""
        return list(self._archive)

    def get_elite_count(self) -> int:
        """Return the current elite count (dynamic in ADAPTIVE mode)."""
        return self._current_elite_count

    def get_turnover_mode(self) -> EliteTurnoverMode:
        """Return the current elite turnover strategy."""
        return self._turnover_mode

    def get_sliding_merit_board(self) -> Optional[Any]:
        """Return the SlidingMeritBoard if active, else None."""
        if isinstance(self._merit_board, SlidingMeritBoard):
            return self._merit_board
        return None

    def get_raw_merit_board(self) -> Optional[Any]:
        """Return the underlying MeritBoard (unwraps SlidingMeritBoard if present)."""
        if isinstance(self._merit_board, SlidingMeritBoard):
            return self._merit_board.board
        return self._merit_board

    def apply_genome_to_minister(self, minister: Any, genome: MinisterGenome) -> None:
        """Apply genome parameters to an actual Minister instance.

        Uses the Minister's set_genome() + set_genome_injector() to establish
        the full genome→LLM injection pipeline, closing the
        「breeding → evolution → behavior → merit → selection」 loop.
        """
        from jarvis.court.genome_injector import GenomeInjector

        if hasattr(minister, "set_genome"):
            minister.set_genome(genome)
        if hasattr(minister, "set_genome_injector"):
            minister.set_genome_injector(GenomeInjector())
        logger.debug("[Evolution] Applied genome+injector to %s: T=%.2f C=%.2f",
                     genome.name, genome.temperature, genome.confidence_baseline)
