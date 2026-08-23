"""Tests for GenomeStore: serialization, roundtrip, atomic write, load safety."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jarvis.court.evolution import MinisterGenome
from jarvis.court.genome_store import GenomeStore


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def sample_genome() -> MinisterGenome:
    return MinisterGenome(
        name="test_minister",
        domain="coding",
        temperature=0.8,
        confidence_baseline=0.9,
        exploration_rate=0.4,
        conservatism=0.3,
        prompt_mutation_rate=0.15,
        specialization_weight=1.2,
        generation=3,
        parent="ancestor",
    )


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


# ── to_dict / from_dict roundtrip ────────────────────────────────────

def test_to_dict_has_all_fields(sample_genome: MinisterGenome) -> None:
    d = GenomeStore.to_dict(sample_genome)
    assert d["name"] == "test_minister"
    assert d["domain"] == "coding"
    assert d["temperature"] == 0.8
    assert d["confidence_baseline"] == 0.9
    assert d["exploration_rate"] == 0.4
    assert d["conservatism"] == 0.3
    assert d["prompt_mutation_rate"] == 0.15
    assert d["specialization_weight"] == 1.2
    assert d["generation"] == 3
    assert d["parent"] == "ancestor"


def test_roundtrip_single(sample_genome: MinisterGenome) -> None:
    d = GenomeStore.to_dict(sample_genome)
    restored = GenomeStore.from_dict(d)
    assert restored.name == sample_genome.name
    assert restored.domain == sample_genome.domain
    assert restored.temperature == sample_genome.temperature
    assert restored.confidence_baseline == sample_genome.confidence_baseline
    assert restored.exploration_rate == sample_genome.exploration_rate
    assert restored.conservatism == sample_genome.conservatism
    assert restored.prompt_mutation_rate == sample_genome.prompt_mutation_rate
    assert restored.specialization_weight == sample_genome.specialization_weight
    assert restored.generation == sample_genome.generation
    assert restored.parent == sample_genome.parent


def test_from_dict_defaults_missing_fields() -> None:
    minimal = {"name": "minimal", "domain": "general"}
    restored = GenomeStore.from_dict(minimal)
    assert restored.temperature == 0.7
    assert restored.confidence_baseline == 0.85
    assert restored.exploration_rate == 0.3
    assert restored.conservatism == 0.5
    assert restored.prompt_mutation_rate == 0.1
    assert restored.specialization_weight == 1.0
    assert restored.generation == 0
    assert restored.parent == ""


# ── save / load ──────────────────────────────────────────────────────

def test_save_and_load(temp_dir: Path) -> None:
    g1 = MinisterGenome(name="m1", domain="d1", temperature=0.5, generation=1)
    g2 = MinisterGenome(name="m2", domain="d2", temperature=0.9, generation=5)

    path = temp_dir / "genomes.json"
    GenomeStore.save(path, [g1, g2], metadata={"cycle": 3})

    genomes, meta = GenomeStore.load(path)
    assert len(genomes) == 2
    assert genomes[0].name == "m1"
    assert genomes[0].temperature == 0.5
    assert genomes[1].name == "m2"
    assert genomes[1].generation == 5
    assert meta["cycle"] == 3


def test_load_missing_file_returns_empty(temp_dir: Path) -> None:
    path = temp_dir / "nonexistent.json"
    genomes, meta = GenomeStore.load(path)
    assert genomes == []
    assert meta == {}


def test_load_corrupt_file_returns_empty(temp_dir: Path) -> None:
    path = temp_dir / "corrupt.json"
    path.write_text("{not json!!!", encoding="utf-8")
    genomes, meta = GenomeStore.load(path)
    assert genomes == []
    assert meta == {}


def test_save_creates_parent_dirs(temp_dir: Path) -> None:
    g = MinisterGenome(name="sole", domain="d")
    path = temp_dir / "deep" / "nested" / "genomes.json"
    GenomeStore.save(path, [g])
    assert path.is_file()

    genomes, _ = GenomeStore.load(path)
    assert len(genomes) == 1
    assert genomes[0].name == "sole"


def test_atomic_write_no_corruption_on_disk(temp_dir: Path) -> None:
    g = MinisterGenome(name="atomic", domain="d")
    path = temp_dir / "atomic.json"

    GenomeStore.save(path, [g])

    # tmp file should not exist after successful save
    tmp_path = path.with_suffix(".tmp")
    assert not tmp_path.exists()

    # content should be valid JSON
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["version"] == 1
    assert len(payload["genomes"]) == 1


def test_save_empty_list(temp_dir: Path) -> None:
    path = temp_dir / "empty.json"
    GenomeStore.save(path, [])
    genomes, meta = GenomeStore.load(path)
    assert genomes == []
    assert meta == {}


def test_roundtrip_multiple_generations(temp_dir: Path) -> None:
    """Verify that generation and parent fields survive save/load."""
    g = MinisterGenome(
        name="evolved", domain="code", generation=42, parent="origin",
    )
    path = temp_dir / "evolved.json"
    GenomeStore.save(path, [g])

    genomes, _ = GenomeStore.load(path)
    assert genomes[0].generation == 42
    assert genomes[0].parent == "origin"


def test_save_overwrites_existing(temp_dir: Path) -> None:
    g1 = MinisterGenome(name="first", domain="d")
    g2 = MinisterGenome(name="second", domain="d")

    path = temp_dir / "overwrite.json"
    GenomeStore.save(path, [g1])
    GenomeStore.save(path, [g2])

    genomes, _ = GenomeStore.load(path)
    assert len(genomes) == 1
    assert genomes[0].name == "second"
