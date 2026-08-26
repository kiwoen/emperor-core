"""
Codex — HUANXIN Code Intelligence Engine.

A hot-pluggable module that connects to the Hermes message bus and provides
code analysis, generation, review, and refactoring capabilities.

Architecture:
    ┌──────────┐     Hermes Bus     ┌──────────┐
    │Orchestrator│ ◄────────────── ► │  Codex   │
    └──────────┘                    └────┬─────┘
                                         │
                                 ┌───────▼───────┐
                                 │   Analyzer     │  AST analysis
                                 │   Generator    │  code generation
                                 │   Reviewer     │  diff review
                                 │   Refactor     │  pattern-based refactoring
                                 └───────────────┘

Each sub-engine communicates via Hermes topics:
- codex.analyze.{lang} → codex.analyze.completed
- codex.generate.{lang} → codex.generate.completed
- codex.review.diff → codex.review.completed
- codex.refactor.{pattern} → codex.refactor.completed
"""

from huanxin.codex.engine import CodexEngine
from huanxin.codex.analyzer import Analyzer
from huanxin.codex.generator import Generator
from huanxin.codex.reviewer import CodeReviewer, ReviewReport

__all__ = ["CodexEngine", "Analyzer", "Generator", "CodeReviewer", "ReviewReport"]
