"""
Consensus — Multi-Agent Debate & Decision-Making Engine.

A hot-pluggable module for the Huanxin system that enables
multi-minister deliberation, cross-critique, and consensus formation.

Architecture:
    ┌──────────────┐     ┌──────────────────────┐
    │   Huanxin    │────▸│   ConsensusEngine     │
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

from huanxin.consensus.engine import ConsensusEngine
from huanxin.consensus.strategies import (
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
