"""
Consensus — Multi-Agent Debate & Decision-Making Engine.

A hot-pluggable module for the Emperor system that enables
multi-minister deliberation, cross-critique, and consensus formation.

Architecture:
    ┌──────────────┐     ┌──────────────────────┐
    │   Emperor    │────▸│   ConsensusEngine     │
    │  deliberate()│     │  ┌──────────────────┐ │
    └──────────────┘     │  │  strategies.py   │ │
                          │  │  - MajorityVote  │ │
                          │  │  - WeightedVote  │ │
                          │  │  - DebateRound   │ │
                          │  │  - BestOfN       │ │
                          │  │  - Synthesis     │ │
                          │  └──────────────────┘ │
                          └──────────────────────┘

Each minister independently processes a task, outputs + reasoning are
collected, cross-critiqued by peers, and a final consensus is formed
using the chosen strategy.
"""

from jarvis.consensus.engine import ConsensusEngine
from jarvis.consensus.strategies import (
    MajorityVote,
    WeightedVote,
    DebateRound,
    BestOfN,
    SynthesisConsensus,
)

__all__ = [
    "ConsensusEngine",
    "MajorityVote",
    "WeightedVote",
    "DebateRound",
    "BestOfN",
    "SynthesisConsensus",
]
