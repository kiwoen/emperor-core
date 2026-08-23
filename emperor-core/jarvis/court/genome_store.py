"""Genome persistence layer — save/load MinisterGenome objects to/from disk.

Evolution progress (generations, crossovers, mutations) produces valuable
genetic material. This module ensures that material survives restarts by
serialising genomes to a JSON file.

Design decisions:
- JSON (not pickle): human-readable, debuggable, version-tolerant
- Atomic write: write to .tmp then rename, preventing corruption on crash
- Roundtrip fidelity: all MinisterGenome fields are primitive types
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.court.evolution import MinisterGenome


class GenomeStore:
    """Serialises and deserialises MinisterGenome objects as JSON."""

    @staticmethod
    def to_dict(genome: "MinisterGenome") -> dict:
        """Convert a single genome to a plain dict."""
        return {
            "name": genome.name,
            "domain": genome.domain,
            "temperature": genome.temperature,
            "confidence_baseline": genome.confidence_baseline,
            "exploration_rate": genome.exploration_rate,
            "conservatism": genome.conservatism,
            "prompt_mutation_rate": genome.prompt_mutation_rate,
            "specialization_weight": genome.specialization_weight,
            "generation": genome.generation,
            "parent": genome.parent,
        }

    @staticmethod
    def from_dict(data: dict) -> "MinisterGenome":
        """Reconstruct a genome from a plain dict."""
        from jarvis.court.evolution import MinisterGenome
        return MinisterGenome(
            name=data["name"],
            domain=data["domain"],
            temperature=data.get("temperature", 0.7),
            confidence_baseline=data.get("confidence_baseline", 0.85),
            exploration_rate=data.get("exploration_rate", 0.3),
            conservatism=data.get("conservatism", 0.5),
            prompt_mutation_rate=data.get("prompt_mutation_rate", 0.1),
            specialization_weight=data.get("specialization_weight", 1.0),
            generation=data.get("generation", 0),
            parent=data.get("parent", ""),
        )

    @staticmethod
    def save(
        path: str | Path,
        genomes: list["MinisterGenome"],
        metadata: dict | None = None,
    ) -> None:
        """Atomically write genomes to a JSON file.

        Metadata (active_count, shadow_count, cycle, etc.) is included
        alongside the genome array for quick inspection.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload: dict = {
            "version": 1,
            "metadata": metadata or {},
            "genomes": [GenomeStore.to_dict(g) for g in genomes],
        }

        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        os.replace(tmp_path, path)  # atomic on same filesystem

    @staticmethod
    def load(path: str | Path) -> tuple[list["MinisterGenome"], dict]:
        """Load genomes from a JSON file.

        Returns (genomes, metadata). Returns ([], {}) if file doesn't
        exist or is corrupt.
        """
        path = Path(path)
        if not path.is_file():
            return [], {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return [], {}

        genomes = [
            GenomeStore.from_dict(d)
            for d in payload.get("genomes", [])
        ]
        metadata = payload.get("metadata", {})
        return genomes, metadata
