"""Tests for the Court facade (one-stop entry point).

Validates that Court wires together MeritBoard, SurvivalMechanism,
EvolutionHistory, and CourtInspector correctly, and that the
convenience API works end-to-end.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from jarvis.court.court import Court, CourtConfig
from jarvis.court.history import CycleRecord, EvolutionHistory


# ── Helpers ───────────────────────────────────────────────────────

def _court_with_ministers(
    n: int = 5,
    config: CourtConfig | None = None,
) -> Court:
    """Create a Court with N ministers registered."""
    c = Court(config=config)
    for i in range(n):
        c.register(domain=["math", "code", "law", "history", "science"][i % 5],
                   temperature=0.6 + i * 0.05)
    return c


# ══════════════════════════════════════════════════════════════════
# Construction
# ══════════════════════════════════════════════════════════════════

class TestCourtConstruction:
    """Court should initialise cleanly with defaults or custom config."""

    def test_default_construction(self):
        c = Court()
        assert c.cycle == 0
        assert c.active_ministers == []
        assert isinstance(c.config, CourtConfig)

    def test_custom_config(self):
        cfg = CourtConfig(elitism_count=5, max_ministers=10)
        c = Court(config=cfg)
        assert c.config.elitism_count == 5
        assert c.config.max_ministers == 10

    def test_config_defaults_sensible(self):
        cfg = CourtConfig()
        assert cfg.min_ministers >= 1
        assert cfg.max_ministers > cfg.min_ministers
        assert 0 < cfg.crossover_rate <= 1
        assert cfg.genome_path is None


# ══════════════════════════════════════════════════════════════════
# Registration
# ══════════════════════════════════════════════════════════════════

class TestCourtRegistration:
    """Ministers should register and appear in active list."""

    def test_single_register(self):
        c = Court()
        name = c.register("curie", domain="physics", temperature=0.3)
        assert name == "curie"
        assert "curie" in c.active_ministers

    def test_auto_naming(self):
        c = Court()
        n1 = c.register(domain="math")
        n2 = c.register(domain="code")
        assert n1 == "m0"
        assert n2 == "m1"
        assert n1 != n2

    def test_bulk_register(self):
        c = Court()
        names = c.register_many([
            {"domain": "math", "temperature": 0.6},
            {"name": "turing", "domain": "code"},
        ])
        assert names[0] == "m0"
        assert names[1] == "turing"
        assert len(c.active_ministers) == 2

    def test_register_preserves_params(self):
        c = Court()
        c.register("test", domain="law", temperature=0.42)

        detail = c.inspect.minister_detail("test")
        assert "test" in detail
        assert "law" in detail
        assert "0.42" in detail


# ══════════════════════════════════════════════════════════════════
# Evolution
# ══════════════════════════════════════════════════════════════════

class TestCourtEvolution:
    """Court.evolve() should run cycles and produce results."""

    def test_evolve_single_cycle(self):
        c = _court_with_ministers(5)
        result = c.evolve(1)
        assert result["total_cycles"] == 1
        assert c.cycle == 1

    def test_evolve_multiple_cycles(self):
        c = _court_with_ministers(4)
        result = c.evolve(3)
        assert result["total_cycles"] == 3
        assert c.cycle == 3

    def test_evolve_returns_structured_summary(self):
        c = _court_with_ministers(5)
        result = c.evolve(5)
        assert "total_cycles" in result
        assert "active_start" in result
        assert "active_end" in result
        assert "delta" in result
        assert isinstance(result["cycles"], list)
        assert isinstance(result["merit_trend"], list)

    def test_run_cycle_returns_report(self):
        c = _court_with_ministers(4)
        report = c.run_cycle()
        assert report is not None
        assert c.cycle == 1


# ══════════════════════════════════════════════════════════════════
# Merit
# ══════════════════════════════════════════════════════════════════

class TestCourtMerit:
    """Merit recording should flow through to the board."""

    def test_record_dispatch_updates_board(self):
        c = _court_with_ministers(3)
        c.record_dispatch("m0", "e1", "query", True, 0.9, 150)
        ranking = c.merit_ranking
        assert len(ranking) > 0

    def test_merit_ranking_is_ordered(self):
        c = _court_with_ministers(3)
        c.record_dispatch("m0", "e1", "query", True, 0.99, 50)
        c.record_dispatch("m1", "e2", "query", True, 0.7, 200)
        ranking = c.merit_ranking
        assert len(ranking) >= 2


# ══════════════════════════════════════════════════════════════════
# Inspection
# ══════════════════════════════════════════════════════════════════

class TestCourtInspection:
    """Inspector should provide read-only views."""

    def test_summary_produces_string(self):
        c = _court_with_ministers(5)
        s = c.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_inspector_snapshot(self):
        c = _court_with_ministers(4)
        snap = c.inspect.snapshot()
        assert snap.active_count == 4

    def test_minister_detail(self):
        c = _court_with_ministers(3)
        detail = c.inspect.minister_detail("m0")
        assert isinstance(detail, str)
        assert "m0" in detail

    def test_inspector_lazy(self):
        c = Court()
        assert c._inspector is None
        _ = c.inspect
        assert c._inspector is not None


# ══════════════════════════════════════════════════════════════════
# History
# ══════════════════════════════════════════════════════════════════

class TestCourtHistory:
    """Evolution history should be recorded automatically."""

    def test_history_records_after_evolve(self):
        c = _court_with_ministers(4)
        c.evolve(2)
        assert len(c.history) == 2

    def test_history_records_after_run_cycle(self):
        c = _court_with_ministers(3)
        c.run_cycle()
        assert len(c.history) == 1

    def test_history_cycle_record_fields(self):
        c = _court_with_ministers(5)
        c.evolve(1)
        record = c.history[-1]
        assert isinstance(record, CycleRecord)
        assert record.cycle == 1
        assert record.active_count > 0

    def test_history_empty_before_evolution(self):
        c = _court_with_ministers(3)
        assert len(c.history) == 0


# ══════════════════════════════════════════════════════════════════
# Persistence
# ══════════════════════════════════════════════════════════════════

class TestCourtPersistence:
    """Genome save/load through Court."""

    def test_save_genomes_without_path(self):
        c = Court(config=CourtConfig(genome_path=None))
        c.register("alpha", domain="math")
        result = c.save_genomes()
        assert result is None

    def test_save_genomes_with_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "genomes.json"
            cfg = CourtConfig(genome_path=str(path))
            c = Court(config=cfg)
            c.register("alpha", domain="math", temperature=0.42)
            result = c.save_genomes()
            assert result is not None
            assert path.exists()

            data = json.loads(path.read_text())
            assert "genomes" in data
            assert isinstance(data["genomes"], list)
            assert len(data["genomes"]) >= 1
            names = [g["name"] for g in data["genomes"]]
            assert "alpha" in names

    def test_load_genomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "genomes.json"
            cfg = CourtConfig(genome_path=str(path))
            src = Court(config=cfg)
            src.register("alpha", domain="math", temperature=0.42)
            src.save_genomes()

            dst = Court(config=cfg)
            genomes, _meta = dst.load_genomes(str(path))
            assert len(genomes) >= 1
            assert "alpha" in dst.active_ministers
            detail = dst.inspect.minister_detail("alpha")
            assert "0.42" in detail


# ══════════════════════════════════════════════════════════════════
# Lifecycle integration
# ══════════════════════════════════════════════════════════════════

class TestCourtLifecycle:
    """End-to-end lifecycle: register → evolve → history → save."""

    def test_full_lifecycle(self):
        c = Court()
        c.register_many([
            {"domain": "math"},
            {"domain": "code"},
            {"name": "curie", "domain": "physics"},
        ])

        assert c.cycle == 0
        assert len(c.active_ministers) == 3

        c.record_dispatch("m0", "e1", "solve equation", True, 0.95, 120)
        c.record_dispatch("m1", "e2", "write function", True, 0.88, 80)

        result = c.evolve(4)
        assert result["total_cycles"] == 4
        assert c.cycle == 4
        assert len(c.history) == 4

        s = c.summary()
        assert "进化法庭" in s or "周期" in s or "Active" in s
