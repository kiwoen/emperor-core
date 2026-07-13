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
        4. Vacancy filled by cloning top performer with mutation

    Key principle: the court has finite capacity. Underperformers
    must make way for potentially better variants.
    """

    # How many probation cycles before elimination
    MAX_PROBATION_CYCLES = 3

    # How many dispatches per probation cycle
    DISPATCHES_PER_CYCLE = 10

    # Minimum court size
    MIN_COURT_SIZE = 4

    def __init__(
        self,
        merit_board: Any = None,
        minister_registry: Optional[dict[str, Any]] = None,
    ) -> None:
        self._merit_board = merit_board
        self._registry = minister_registry or {}
        self._statuses: dict[str, MinisterStatus] = {}
        self._probation_cycles: dict[str, int] = {}
        self._genomes: dict[str, MinisterGenome] = {}
        self._archive: list[MinisterGenome] = []  # Eliminated genomes
        self._events: list[EvolutionEvent] = []
        self._cycle_count = 0

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
        self._genomes[name] = genome

    def register_shadow(self, name: str, domain: str = "") -> None:
        """Register a shadow minister (trains but doesn't vote)."""
        self._statuses[name] = MinisterStatus.SHADOW
        self._probation_cycles[name] = 0
        self._genomes[name] = MinisterGenome(
            name=name, domain=domain, generation=0,
        )

    # ------------------------------------------------------------------
    # Evolution cycle
    # ------------------------------------------------------------------

    def run_evolution_cycle(self) -> EvolutionReport:
        """Execute one complete evolution cycle.

        Returns a report of all actions taken.
        """
        self._cycle_count += 1
        actions: list[EvolutionEvent] = []
        systemic_issues: list[str] = []
        recommendations: list[str] = []

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
        """Identify active ministers who should enter probation."""
        actions: list[EvolutionEvent] = []
        issues: list[str] = []

        if self._merit_board is None:
            return actions, issues

        candidates = self._merit_board.get_probation_candidates()
        for minister in candidates:
            current_status = self._statuses.get(minister, MinisterStatus.ACTIVE)
            # Only active ministers can enter probation
            if current_status != MinisterStatus.ACTIVE:
                continue

            merit = self._merit_board.compute_merit(minister)
            self._statuses[minister] = MinisterStatus.PROBATION
            self._probation_cycles[minister] = 0  # initialized, incremented in _process_probationers
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
        If probation_cycles >= MAX → eliminate.
        If merit has recovered → remove probation.
        """
        actions: list[EvolutionEvent] = []
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
        """Demote active ministers with critically low merit to shadow."""
        actions: list[EvolutionEvent] = []
        for minister, status in list(self._statuses.items()):
            if status != MinisterStatus.ACTIVE:
                continue
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
        """Fill court vacancies by cloning top performers.

        Vacancy = a minister was eliminated but we're below ideal court size.
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

        # Find top performer to clone
        top_name, top_merit = self._find_top_performer()
        if top_name is None:
            return actions

        # Clone with mutation
        new_name = self._generate_clone_name(top_name)
        parent_genome = self._genomes.get(top_name)
        if parent_genome is None:
            return actions

        mutated = self._mutate_genome(parent_genome, new_name)
        self._genomes[new_name] = mutated
        self._statuses[new_name] = MinisterStatus.SHADOW
        self._probation_cycles[new_name] = 0

        event = EvolutionEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            minister=new_name,
            action=EvolutionAction.CLONE_MUTATE,
            reason=f"克隆自 {top_name}（功勋 {top_merit:.1f}）填补空缺",
            previous_merit=0,
            details={
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
    # Genome operations
    # ------------------------------------------------------------------

    def _mutate_genome(
        self, parent: MinisterGenome, new_name: str
    ) -> MinisterGenome:
        """Create a mutated clone of a parent genome.

        Mutation rules:
        - Temperature: perturb by ±0.1 (capped at [0.2, 1.0])
        - Confidence baseline: perturb by ±0.05 (capped at [0.3, 0.95])
        - Exploration rate: flip with 30% chance
        - Prompt mutation rate: slight increase each generation
        """
        temp_delta = random.uniform(-0.15, 0.15)
        conf_delta = random.uniform(-0.08, 0.08)

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

    def _can_eliminate(self) -> bool:
        """Check if we can eliminate without going below minimum court size."""
        active_and_shadow = sum(
            1 for s in self._statuses.values()
            if s in (MinisterStatus.ACTIVE, MinisterStatus.SHADOW)
        )
        return active_and_shadow > self.MIN_COURT_SIZE

    def _find_top_performer(self) -> tuple[Optional[str], float]:
        """Find the highest-merit active minister."""
        best_name: Optional[str] = None
        best_merit = 0.0
        for name, status in self._statuses.items():
            if status not in (MinisterStatus.ACTIVE, MinisterStatus.SHADOW):
                continue
            merit = (
                self._merit_board.compute_merit(name)
                if self._merit_board else 0
            )
            if merit > best_merit:
                best_merit = merit
                best_name = name
        return best_name, best_merit

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_status(self, minister: str) -> MinisterStatus:
        """Get the evolutionary status of a minister."""
        return self._statuses.get(minister, MinisterStatus.ACTIVE)

    def get_genome(self, minister: str) -> Optional[MinisterGenome]:
        """Get the evolvable genome of a minister."""
        return self._genomes.get(minister)

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

    def apply_genome_to_minister(self, minister: Any, genome: MinisterGenome) -> None:
        """Apply genome parameters to an actual Minister instance.

        This bridges the evolution engine with the runtime Minister objects.
        """
        if hasattr(minister, "_current_temperature"):
            minister._current_temperature = genome.temperature
        if hasattr(minister, "_confidence_baseline"):
            minister._confidence_baseline = genome.confidence_baseline
        logger.debug("[Evolution] Applied genome to %s: T=%.2f C=%.2f",
                     genome.name, genome.temperature, genome.confidence_baseline)
