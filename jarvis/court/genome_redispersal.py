"""Genome re-dispersion — cure minister-population homogeneity.

Root cause of the ``[Diversity] Crisis cycle N/5 (score=0.000, similarity=1.000)``
groupthink collapse:

    Of the 6 ``MinisterGenome`` genes, only ``temperature`` and
    ``confidence_baseline`` are ever varied. The other four
    (``exploration_rate``, ``conservatism``, ``prompt_mutation_rate``,
    ``specialization_weight``) are seeded at dataclass defaults for *every*
    minister and are never evolved by the normal loop, so all ministers share
    a byte-identical 6-gene vector -> cosine similarity = 1.0.

This module provides the building blocks for two complementary fixes:

* :data:`ARCHETYPES` -- a spread of "personality" gene vectors so a freshly
  registered court is diverse from ``t=0`` (Layer 1: diverse seeding).
* :func:`genomic_diversity` / :func:`redisperse` -- measure and, when a loaded
  population is too homogeneous, deterministically re-assign the four dormant
  genes across distinct archetypes (Layer 2: restart self-heal for an already
  deployed homogeneous ``genome_state.json``).

Both layers are deterministic (no RNG) so restarts are reproducible.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Tuple

# Spread of personality gene vectors. Each archetype pushes the four dormant
# genes to a distinct "corner" of the personality space so any two are clearly
# non-collinear. Calibrated so a freshly-seeded or re-dispersed court reaches a
# *floor* genomic diversity of ~0.18 (with constant temperature/confidence) —
# comfortably above DIVERSITY_CRISIS_THRESHOLD (0.15) — while keeping every gene
# strictly inside its valid range (exploration_rate/conservatism/
# prompt_mutation_rate in [0,1]; specialization_weight strictly inside the
# [0.5, 2.0] band that ``genome_injector`` consumes).
ARCHETYPES: list[dict] = [
    {"exploration_rate": 1.00, "conservatism": 0.05, "prompt_mutation_rate": 1.00, "specialization_weight": 1.95},
    {"exploration_rate": 0.05, "conservatism": 1.00, "prompt_mutation_rate": 0.05, "specialization_weight": 0.55},
    {"exploration_rate": 0.50, "conservatism": 0.50, "prompt_mutation_rate": 0.50, "specialization_weight": 1.00},
    {"exploration_rate": 1.00, "conservatism": 0.50, "prompt_mutation_rate": 0.05, "specialization_weight": 1.35},
    {"exploration_rate": 0.05, "conservatism": 0.05, "prompt_mutation_rate": 1.00, "specialization_weight": 0.85},
    {"exploration_rate": 0.50, "conservatism": 1.00, "prompt_mutation_rate": 0.50, "specialization_weight": 0.60},
]

# The six genes that define a minister's feature vector (order matters for
# cosine comparison; must match DiversityMonitor._extract_feature_vector).
_GENE_KEYS = (
    "temperature",
    "confidence_baseline",
    "exploration_rate",
    "conservatism",
    "prompt_mutation_rate",
    "specialization_weight",
)


def _feature_vector(g: Any) -> list[float]:
    return [float(getattr(g, k, 0.0)) for k in _GENE_KEYS]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def genomic_diversity(genomes: Dict[str, Any]) -> float:
    """Pairwise-cosine genomic diversity of a {name: MinisterGenome} mapping.

    Returns 1.0 for <2 ministers, else ``1 - mean_pairwise_cosine``.
    """
    gens = [g for g in genomes.values() if g is not None]
    n = len(gens)
    if n < 2:
        return 1.0
    vecs = [_feature_vector(g) for g in gens]
    sims: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            sims.append(_cosine(vecs[i], vecs[j]))
    avg = sum(sims) / len(sims)
    return max(0.0, min(1.0, 1.0 - avg))


def redisperse(
    genomes: Dict[str, Any],
    threshold: float = 0.10,
) -> Tuple[Dict[str, Any], bool]:
    """Re-assign the four dormant personality genes for every minister when the
    population is genetically near-identical.

    Assignment is deterministic by sorted-minister-name so each minister gets a
    stable, distinct archetype -- guaranteeing a spread-out, reproducible
    population. Mutates the ``MinisterGenome`` objects in ``genomes`` in place.

    Returns ``(genomes, changed)``.
    """
    if genomic_diversity(genomes) >= threshold:
        return genomes, False
    for idx, name in enumerate(sorted(genomes.keys())):
        g = genomes.get(name)
        if g is None:
            continue
        arch = ARCHETYPES[idx % len(ARCHETYPES)]
        g.exploration_rate = float(arch["exploration_rate"])
        g.conservatism = float(arch["conservatism"])
        g.prompt_mutation_rate = float(arch["prompt_mutation_rate"])
        g.specialization_weight = float(arch["specialization_weight"])
    return genomes, True


def archetype_for_index(index: int) -> dict:
    """Return the archetype dict for a 0-based registration index (round-robin)."""
    return dict(ARCHETYPES[index % len(ARCHETYPES)])
