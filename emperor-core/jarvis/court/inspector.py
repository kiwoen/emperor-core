"""Runtime introspection for the evolutionary court.

CourtInspector provides read-only access to the internal state of a
SurvivalMechanism — ministers, genomes, merits, statuses, and cycle
history — without importing heavy dependencies or mutating state.

Use it to build dashboards, CLIs, or monitoring UIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.court.evolution import (
        EvolutionEvent,
        EvolutionReport,
        MinisterGenome,
        MinisterStatus,
        SurvivalMechanism,
    )


@dataclass
class MinisterSnapshot:
    """Immutable snapshot of one minister's current state."""

    name: str
    domain: str
    status: str
    merit: float
    generation: int
    temperature: float
    confidence_baseline: float
    exploration_rate: float
    conservatism: float
    specialization_weight: float


@dataclass
class CourtSnapshot:
    """Full snapshot of the evolutionary court."""

    cycle: int
    total_ministers: int
    active_count: int
    shadow_count: int
    probation_count: int
    eliminated_count: int
    ministers: list[MinisterSnapshot] = field(default_factory=list)
    last_report: dict | None = None


class CourtInspector:
    """Read-only inspector for SurvivalMechanism.

    Usage::

        sm = SurvivalMechanism(...)
        inspector = CourtInspector(sm)
        print(inspector.summary())

        # Or get a structured snapshot:
        snap = inspector.snapshot()
        for m in snap.ministers:
            print(f"{m.name}: merit={m.merit:.1f}")
    """

    def __init__(self, mechanism: "SurvivalMechanism") -> None:
        self._sm = mechanism

    # ── Core inspection ──────────────────────────────────────────────

    def snapshot(self) -> CourtSnapshot:
        """Capture a full snapshot of the court's current state."""
        sm = self._sm

        active = sm.get_active_ministers()

        statuses: dict[str, str] = {}
        for name in sm._statuses:
            statuses[name] = sm._statuses[name].name

        minister_snapshots: list[MinisterSnapshot] = []
        for name, genome in sm._genomes.items():
            merit = 0.0
            if sm._merit_board is not None:
                merit = sm._merit_board.compute_merit(name)

            status = statuses.get(name, "UNKNOWN")
            minister_snapshots.append(
                MinisterSnapshot(
                    name=name,
                    domain=genome.domain,
                    status=status,
                    merit=round(merit, 2),
                    generation=genome.generation,
                    temperature=genome.temperature,
                    confidence_baseline=genome.confidence_baseline,
                    exploration_rate=genome.exploration_rate,
                    conservatism=genome.conservatism,
                    specialization_weight=genome.specialization_weight,
                )
            )

        # Sort: active first, then by merit desc
        minister_snapshots.sort(
            key=lambda m: (
                0 if m.status == "ACTIVE" else 1 if m.status == "SHADOW" else 2,
                -m.merit,
            )
        )

        active_count = len(active)
        shadow_count = sum(
            1 for s in statuses.values() if s == "SHADOW"
        )
        probation_count = sum(
            1 for s in statuses.values() if s == "PROBATION"
        )
        eliminated_count = sum(
            1 for s in statuses.values() if s == "ELIMINATED"
        )

        return CourtSnapshot(
            cycle=sm._cycle_count,
            total_ministers=len(sm._genomes),
            active_count=active_count,
            shadow_count=shadow_count,
            probation_count=probation_count,
            eliminated_count=eliminated_count,
            ministers=minister_snapshots,
        )

    def summary(self) -> str:
        """Return a human-readable summary of the court."""
        snap = self.snapshot()

        lines = [
            f"=== 进化法庭 第 {snap.cycle} 周期 ===",
            f"总数: {snap.total_ministers} "
            f"(Active {snap.active_count}, Shadow {snap.shadow_count}, "
            f"Probation {snap.probation_count}, Eliminated {snap.eliminated_count})",
            "",
            "活跃大臣 (Active Ministers):",
        ]

        active = [m for m in snap.ministers if m.status == "ACTIVE"]
        if active:
            for m in active[:10]:  # top 10
                lines.append(
                    f"  {m.name:<20} 功绩={m.merit:>5.1f}  "
                    f"基因={m.generation}  领域={m.domain}  "
                    f"T={m.temperature:.2f}"
                )
        else:
            lines.append("  (无)")

        lines.append("")
        lines.append("影子内阁 (Shadow Cabinet):")
        shadows = [m for m in snap.ministers if m.status == "SHADOW"]
        if shadows:
            for m in shadows[:5]:
                lines.append(
                    f"  {m.name:<20} 功绩={m.merit:>5.1f}  基因={m.generation}"
                )
        else:
            lines.append("  (无)")

        return "\n".join(lines)

    def minister_detail(self, name: str) -> str | None:
        """Return a detailed report for one minister, or None if not found."""
        genome = self._sm._genomes.get(name)
        if genome is None:
            return None

        merit = 0.0
        if self._sm._merit_board is not None:
            merit = self._sm._merit_board.compute_merit(name)

        status = "UNKNOWN"
        if name in self._sm._statuses:
            status = self._sm._statuses[name].name

        return "\n".join([
            f"大臣: {genome.name}",
            f"领域: {genome.domain}",
            f"状态: {status}",
            f"功绩: {merit:.2f}",
            f"世代: {genome.generation}",
            f"先祖: {genome.parent or '(始祖)'}",
            f"",
            f"基因组:",
            f"  temperature:          {genome.temperature:.4f}",
            f"  confidence_baseline:  {genome.confidence_baseline:.4f}",
            f"  exploration_rate:     {genome.exploration_rate:.4f}",
            f"  conservatism:         {genome.conservatism:.4f}",
            f"  prompt_mutation_rate: {genome.prompt_mutation_rate:.4f}",
            f"  specialization_weight:{genome.specialization_weight:.4f}",
        ])
