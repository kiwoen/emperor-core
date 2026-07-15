"""Structured evolution history recorder.

EvolutionHistory captures a time-series of CourtSnapshots and
EvolutionReports across cycles, enabling trend analysis, comparison,
and CSV/JSON export.

Integrated into SurvivalMechanism via an optional 'recorder' parameter.
When enabled, every run_evolution_cycle() call automatically appends
a record.

Design:
- Immutable records — once written, never modified
- Lazy snapshot — snapshot() is called only when recording is active
- Export — JSON and CSV formats for external analysis tools
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.court.evolution import EvolutionReport
    from jarvis.court.inspector import CourtSnapshot


@dataclass
class CycleRecord:
    """One cycle's worth of evolution data."""

    cycle: int
    active_count: int
    shadow_count: int
    probation_count: int
    eliminated_count: int
    total_ministers: int
    new_spawns: int
    actions_taken: list[str]
    systemic_issues: list[str]
    recommendations: list[str]
    # Merit distribution
    merit_mean: float
    merit_median: float
    merit_max: float
    merit_min: float
    # Diversity
    temperature_variance: float
    domain_count: int


class EvolutionHistory:
    """Append-only sequence of CycleRecords."""

    def __init__(self) -> None:
        self._records: list[CycleRecord] = []

    # ── Recording ──────────────────────────────────────────────────────

    def record(
        self,
        report: "EvolutionReport",
        snapshot: "CourtSnapshot",
    ) -> CycleRecord:
        """Create a CycleRecord from an EvolutionReport + CourtSnapshot."""
        merits = [m.merit for m in snapshot.ministers]
        temps = [m.temperature for m in snapshot.ministers]
        domains = {m.domain for m in snapshot.ministers}

        if merits:
            sorted_merits = sorted(merits)
            mid = len(sorted_merits) // 2
            merit_mean = sum(merits) / len(merits)
            merit_median = (
                sorted_merits[mid]
                if len(sorted_merits) % 2 == 1
                else (sorted_merits[mid - 1] + sorted_merits[mid]) / 2
            )
            merit_max = sorted_merits[-1]
            merit_min = sorted_merits[0]
        else:
            merit_mean = merit_median = merit_max = merit_min = 0.0

        if len(temps) > 1:
            m = sum(temps) / len(temps)
            temperature_variance = sum((t - m) ** 2 for t in temps) / len(temps)
        else:
            temperature_variance = 0.0

        record = CycleRecord(
            cycle=report.cycle,
            active_count=report.active_count,
            shadow_count=report.shadow_count,
            probation_count=snapshot.probation_count,
            eliminated_count=snapshot.eliminated_count,
            total_ministers=snapshot.total_ministers,
            new_spawns=report.new_spawns,
            actions_taken=[a.action.name for a in report.actions_taken],
            systemic_issues=list(report.systemic_issues),
            recommendations=list(report.recommendations),
            merit_mean=round(merit_mean, 2),
            merit_median=round(merit_median, 2),
            merit_max=round(merit_max, 2),
            merit_min=round(merit_min, 2),
            temperature_variance=round(temperature_variance, 6),
            domain_count=len(domains),
        )
        self._records.append(record)
        return record

    # ── Query ──────────────────────────────────────────────────────────

    def get_cycle(self, cycle: int) -> CycleRecord | None:
        """Retrieve a specific cycle by number (1-indexed)."""
        for r in self._records:
            if r.cycle == cycle:
                return r
        return None

    def last(self) -> CycleRecord | None:
        """Most recent cycle record."""
        return self._records[-1] if self._records else None

    def trend(self, field: str) -> list[float]:
        """Extract a numeric field across all cycles as a time series.

        Valid fields: active_count, shadow_count, total_ministers,
        new_spawns, merit_mean, merit_median, merit_max, merit_min,
        temperature_variance, domain_count.
        """
        if not self._records:
            return []
        valid_fields = {f.name for f in dataclasses.fields(CycleRecord)}
        if field not in valid_fields:
            raise ValueError(f"Unknown field: {field}")
        return [getattr(r, field) for r in self._records]

    def compare_cycles(self, c1: int, c2: int) -> dict:
        """Side-by-side comparison of two cycles."""
        r1 = self.get_cycle(c1)
        r2 = self.get_cycle(c2)
        if r1 is None or r2 is None:
            return {"error": "Cycle not found"}

        return {
            "cycle_a": c1,
            "cycle_b": c2,
            "active_count": f"{r1.active_count} → {r2.active_count}",
            "total_ministers": f"{r1.total_ministers} → {r2.total_ministers}",
            "merit_mean": f"{r1.merit_mean} → {r2.merit_mean}",
            "temperature_variance": f"{r1.temperature_variance} → {r2.temperature_variance}",
            "new_spawns": f"{r1.new_spawns} → {r2.new_spawns}",
            "domain_count": f"{r1.domain_count} → {r2.domain_count}",
        }

    # ── Export ─────────────────────────────────────────────────────────

    def to_json(self, path: str | None = None) -> str:
        """Serialize all records to JSON string or file."""
        data = [self._record_to_dict(r) for r in self._records]
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(json_str)
        return json_str

    def to_csv(self, path: str | None = None) -> str:
        """Export all records as CSV string or file."""
        if not self._records:
            output = io.StringIO()
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    "cycle", "active_count", "shadow_count", "probation_count",
                    "eliminated_count", "total_ministers", "new_spawns",
                    "merit_mean", "merit_median", "merit_max", "merit_min",
                    "temperature_variance", "domain_count",
                ],
                extrasaction="ignore",
            )
            writer.writeheader()
            return output.getvalue()

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "cycle", "active_count", "shadow_count", "probation_count",
                "eliminated_count", "total_ministers", "new_spawns",
                "merit_mean", "merit_median", "merit_max", "merit_min",
                "temperature_variance", "domain_count",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        for r in self._records:
            writer.writerow(self._record_to_dict(r))
        csv_str = output.getvalue()

        if path:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(csv_str)
        return csv_str

    # ── Internals ──────────────────────────────────────────────────────

    @property
    def cycle_count(self) -> int:
        return len(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: int) -> CycleRecord:
        return self._records[index]

    def _record_to_dict(self, r: CycleRecord) -> dict:
        return {
            "cycle": r.cycle,
            "active_count": r.active_count,
            "shadow_count": r.shadow_count,
            "probation_count": r.probation_count,
            "eliminated_count": r.eliminated_count,
            "total_ministers": r.total_ministers,
            "new_spawns": r.new_spawns,
            "actions_taken": r.actions_taken,
            "systemic_issues": r.systemic_issues,
            "recommendations": r.recommendations,
            "merit_mean": r.merit_mean,
            "merit_median": r.merit_median,
            "merit_max": r.merit_max,
            "merit_min": r.merit_min,
            "temperature_variance": r.temperature_variance,
            "domain_count": r.domain_count,
        }

    def _read_from_json(self, path: str) -> None:
        """Load records from a JSON file (inverse of to_json)."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        for d in data:
            self._records.append(CycleRecord(
                cycle=d["cycle"],
                active_count=d["active_count"],
                shadow_count=d.get("shadow_count", 0),
                probation_count=d.get("probation_count", 0),
                eliminated_count=d.get("eliminated_count", 0),
                total_ministers=d.get("total_ministers", 0),
                new_spawns=d.get("new_spawns", 0),
                actions_taken=d.get("actions_taken", []),
                systemic_issues=d.get("systemic_issues", []),
                recommendations=d.get("recommendations", []),
                merit_mean=d.get("merit_mean", 0.0),
                merit_median=d.get("merit_median", 0.0),
                merit_max=d.get("merit_max", 0.0),
                merit_min=d.get("merit_min", 0.0),
                temperature_variance=d.get("temperature_variance", 0.0),
                domain_count=d.get("domain_count", 0),
            ))
