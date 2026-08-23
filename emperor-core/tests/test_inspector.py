"""Tests for CourtInspector: snapshot, summary, minister_detail."""

from __future__ import annotations

import pytest

from jarvis.court.evolution import SurvivalMechanism
from jarvis.court.inspector import CourtInspector
from jarvis.court.merit_board import MeritBoard


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def populated_court() -> SurvivalMechanism:
    """Court with 3 active, 1 shadow minister and a merit board."""
    board = MeritBoard()
    sm = SurvivalMechanism(
        merit_board=board,
        enable_sliding_merit=False,
        enable_auto_breeding=False,
    )
    sm.register_minister("alice", domain="coding", temperature=0.7)
    sm.register_minister("bob", domain="writing", temperature=0.9)
    sm.register_minister("carol", domain="math", temperature=0.5)
    sm.register_shadow("dave", domain="art")

    # Give some merit
    board.record_dispatch("alice", "e1", "test", success=True, confidence=0.9)
    board.record_dispatch("alice", "e2", "test", success=True, confidence=0.85)
    board.record_dispatch("bob", "e3", "test", success=False, confidence=0.3)
    board.record_dispatch("carol", "e4", "test", success=True, confidence=0.95)
    board.record_dispatch("dave", "e5", "test", success=True, confidence=0.7)

    return sm


# ── snapshot ──────────────────────────────────────────────────────────

def test_snapshot_counts(populated_court: SurvivalMechanism) -> None:
    inspector = CourtInspector(populated_court)
    snap = inspector.snapshot()

    assert snap.total_ministers == 4
    assert snap.active_count == 3
    assert snap.shadow_count == 1
    assert snap.probation_count == 0
    assert snap.eliminated_count == 0
    assert snap.cycle == 0


def test_snapshot_ministers_sorted(populated_court: SurvivalMechanism) -> None:
    inspector = CourtInspector(populated_court)
    snap = inspector.snapshot()

    # Active should come before shadow, sorted by merit desc
    active_names = [m.name for m in snap.ministers if m.status == "ACTIVE"]
    shadow_names = [m.name for m in snap.ministers if m.status == "SHADOW"]
    assert set(active_names) == {"alice", "carol", "bob"}
    assert shadow_names == ["dave"]
    # Verify descending merit order
    active_merits = [m.merit for m in snap.ministers if m.status == "ACTIVE"]
    assert active_merits == sorted(active_merits, reverse=True)


def test_snapshot_genome_fields(populated_court: SurvivalMechanism) -> None:
    inspector = CourtInspector(populated_court)
    snap = inspector.snapshot()

    alice = next(m for m in snap.ministers if m.name == "alice")
    assert alice.domain == "coding"
    assert alice.temperature == 0.7
    assert alice.generation == 0
    assert alice.status == "ACTIVE"


def test_snapshot_empty_court() -> None:
    sm = SurvivalMechanism(enable_sliding_merit=False, enable_auto_breeding=False)
    inspector = CourtInspector(sm)
    snap = inspector.snapshot()

    assert snap.total_ministers == 0
    assert snap.active_count == 0
    assert snap.ministers == []


# ── summary ───────────────────────────────────────────────────────────

def test_summary_returns_string(populated_court: SurvivalMechanism) -> None:
    inspector = CourtInspector(populated_court)
    text = inspector.summary()

    assert "进化法庭" in text
    assert "alice" in text
    assert "dave" in text
    assert "活跃大臣" in text or "Active Ministers" in text
    assert "影子内阁" in text or "Shadow Cabinet" in text


def test_summary_empty_court() -> None:
    sm = SurvivalMechanism(enable_sliding_merit=False, enable_auto_breeding=False)
    inspector = CourtInspector(sm)
    text = inspector.summary()
    assert "(无)" in text


# ── minister_detail ──────────────────────────────────────────────────

def test_minister_detail_found(populated_court: SurvivalMechanism) -> None:
    inspector = CourtInspector(populated_court)
    detail = inspector.minister_detail("alice")

    assert detail is not None
    assert "alice" in detail
    assert "coding" in detail
    assert "temperature" in detail
    assert "0.7000" in detail


def test_minister_detail_not_found(populated_court: SurvivalMechanism) -> None:
    inspector = CourtInspector(populated_court)
    detail = inspector.minister_detail("nonexistent")

    assert detail is None


def test_minister_detail_includes_parent(populated_court: SurvivalMechanism) -> None:
    inspector = CourtInspector(populated_court)
    detail = inspector.minister_detail("alice")

    assert "(始祖)" in detail


# ── after evolution cycle ────────────────────────────────────────────

def test_snapshot_after_cycle(populated_court: SurvivalMechanism) -> None:
    populated_court.run_evolution_cycle()
    inspector = CourtInspector(populated_court)
    snap = inspector.snapshot()

    assert snap.cycle == 1
    assert snap.total_ministers > 0
