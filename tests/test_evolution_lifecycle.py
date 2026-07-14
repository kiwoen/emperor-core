"""End-to-end evolution lifecycle integration tests.

Validates the full 「注册 → 繁殖 → 进化 → 持久化 → 重载 → 继续」 loop:
1. Create court with ministers
2. Run evolution cycles (merit, demote, promote, breed, persist)
3. Save genomes to disk
4. Recreate court from persisted genomes and config
5. Resume evolution — verify continuity
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jarvis.court.config import SurvivalConfig
from jarvis.court.evolution import SurvivalMechanism
from jarvis.court.genome_store import GenomeStore
from jarvis.court.inspector import CourtInspector
from jarvis.court.merit_board import MeritBoard


# ── Helpers ───────────────────────────────────────────────────────────

def _record_cycle(
    sm: SurvivalMechanism,
    board: MeritBoard,
    cycle: int,
) -> None:
    """Simulate one cycle of dispatches and evolution."""
    for name in sm.get_active_ministers():
        board.record_dispatch(
            name,
            f"edict_{cycle}_{name}",
            "test",
            success=True,
            confidence=0.7 + 0.05 * (hash(name) % 5),
        )
    sm.run_evolution_cycle()


# ── Full lifecycle ────────────────────────────────────────────────────

def test_full_lifecycle(tmp_path: Path) -> None:
    """Register → evolve → persist → reload → resume."""
    genome_path = str(tmp_path / "genomes.json")

    # ── Phase 1: Create and evolve ──────────────────────────────
    board = MeritBoard()
    sm = SurvivalMechanism(
        merit_board=board,
        enable_sliding_merit=False,
        enable_auto_breeding=True,
        breeding_cooldown=1,
        max_breed_per_cycle=1,
        genome_path=genome_path,
    )
    sm.register_minister("alpha", domain="code", temperature=0.7)
    sm.register_minister("beta", domain="writing", temperature=0.9)
    sm.register_minister("gamma", domain="math", temperature=0.5)

    for i in range(3):
        _record_cycle(sm, board, i)

    # Verify evolution happened
    inspector = CourtInspector(sm)
    snap1 = inspector.snapshot()
    assert snap1.cycle == 3
    assert snap1.total_ministers >= 3  # breeding may have added more

    # ── Phase 2: Verify genomes persisted to disk ───────────────
    assert os.path.isfile(genome_path)
    loaded_genomes, meta = GenomeStore.load(genome_path)
    assert len(loaded_genomes) >= 3
    assert meta["cycle"] == 3

    # ── Phase 3: Recreate from persisted state ──────────────────
    board2 = MeritBoard()
    sm2 = SurvivalMechanism(
        merit_board=board2,
        enable_sliding_merit=False,
        enable_auto_breeding=True,
        breeding_cooldown=1,
        max_breed_per_cycle=1,
        genome_path=genome_path,
    )
    # Re-register ministers that were loaded from persistence
    for g in loaded_genomes:
        if g.name not in sm2._genomes:
            sm2.register_minister(
                g.name, domain=g.domain, temperature=g.temperature,
            )

    inspector2 = CourtInspector(sm2)
    snap2 = inspector2.snapshot()
    assert snap2.total_ministers >= 3

    # ── Phase 4: Resume evolution ───────────────────────────────
    for i in range(2):
        _record_cycle(sm2, board2, i + 4)

    snap3 = inspector2.snapshot()
    assert snap3.cycle == 2  # new instance, starts from 0

    # Verify genomes persisted after resume
    loaded2, meta2 = GenomeStore.load(genome_path)
    assert meta2["cycle"] == 2
    assert len(loaded2) >= 3


# ── Config-driven lifecycle ───────────────────────────────────────────

def test_config_driven_lifecycle(tmp_path: Path) -> None:
    """Use SurvivalConfig + from_config for the full workflow."""
    genome_path = str(tmp_path / "genomes.json")

    config = SurvivalConfig(
        elitism_count=2,
        enable_sliding_merit=False,
        enable_auto_breeding=True,
        breeding_cooldown=1,
        max_breed_per_cycle=1,
        genome_path=genome_path,
    )

    board = MeritBoard()
    sm = SurvivalMechanism.from_config(config, merit_board=board)
    sm.register_minister("one", domain="code")
    sm.register_minister("two", domain="docs")

    for i in range(2):
        _record_cycle(sm, board, i)

    snap = CourtInspector(sm).snapshot()
    assert snap.cycle == 2
    assert snap.total_ministers >= 2


# ── Persistence continuity: genome values survive roundtrip ───────────

def test_genome_values_survive_persist_roundtrip(tmp_path: Path) -> None:
    """Custom genome parameters must survive save→load→recreate."""
    genome_path = str(tmp_path / "genomes.json")

    board = MeritBoard()
    sm = SurvivalMechanism(
        merit_board=board,
        enable_sliding_merit=False,
        enable_auto_breeding=False,
        genome_path=genome_path,
    )
    sm.register_minister(
        "custom", domain="code",
        temperature=0.42,
        confidence_baseline=0.88,
    )
    # Direct save (no evolution cycle — avoid auto-tune mutation)
    sm.save_genomes()

    loaded, _ = GenomeStore.load(genome_path)
    custom = next(g for g in loaded if g.name == "custom")
    assert custom.temperature == 0.42
    assert custom.confidence_baseline == 0.88
    assert custom.exploration_rate == 0.3  # default (not passed to register_minister)


# ── Error resilience ──────────────────────────────────────────────────

def test_corrupt_genome_file_graceful(tmp_path: Path) -> None:
    """A corrupt genome file should not crash loading."""
    path = tmp_path / "corrupt.json"
    path.write_text("not json", encoding="utf-8")

    sm = SurvivalMechanism(
        enable_sliding_merit=False,
        enable_auto_breeding=False,
        genome_path=str(path),
    )
    # Should still work — just no genomes loaded
    assert sm._cycle_count == 0


def test_missing_genome_file_graceful(tmp_path: Path) -> None:
    """Missing genome file should not crash loading."""
    path = str(tmp_path / "nonexistent.json")

    sm = SurvivalMechanism(
        enable_sliding_merit=False,
        enable_auto_breeding=False,
        genome_path=path,
    )
    assert sm._cycle_count == 0


# ── Evolution produces diversity ──────────────────────────────────────

def test_evolution_produces_genetic_diversity(tmp_path: Path) -> None:
    """Over multiple cycles, the population genome should diversify."""
    genome_path = str(tmp_path / "genomes.json")

    board = MeritBoard()
    sm = SurvivalMechanism(
        merit_board=board,
        enable_sliding_merit=False,
        enable_auto_breeding=True,
        breeding_cooldown=1,
        max_breed_per_cycle=2,
        genome_path=genome_path,
    )
    sm.register_minister("a", domain="code", temperature=0.7)
    sm.register_minister("b", domain="code", temperature=0.7)

    for i in range(4):
        _record_cycle(sm, board, i)

    snap = CourtInspector(sm).snapshot()
    # After 4 cycles of breeding, should have > 2 ministers
    assert snap.total_ministers > 2

    # Verify diversity: not all temperatures are identical
    temps = {m.temperature for m in snap.ministers}
    assert len(temps) > 1, f"All ministers have same temperature: {temps}"
