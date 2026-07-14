"""
Imperial Court System (朝堂系统) — multi-agent deliberation architecture.

The Court is a microcosm of a Chinese imperial court: the Emperor (天子)
receives petitions (user intents), analyzes them, and dispatches edicts
to specialized Ministers (大臣). Each Minister is modeled after a real
world-class AI, embodying its specific strengths. Ministers deliberate
independently (parallel), submit memorials (reports), and the Emperor
synthesizes the final decree.

Key roles:
    丞相 (Chancellor)     — GPT-style general reasoning & task decomposition
    御史大夫 (Grand Censor) — Claude-style long-text review & safety compliance
    太史令 (Grand Historian) — Perplexity-style real-time search & fact-checking
    工部尚书 (Works Minister) — DeepSeek-style code engineering & debugging
    太常 (Ceremonies)      — Gemini-style multimodal understanding
    大司农 (Finance)       — cost optimization & resource management
    太卜 (Grand Diviner)   — scientific reasoning & complex prediction
    卫尉 (Guard Captain)   — security auditing & privacy protection

Evolution: each minister self-evolves through experience — adjusting
confidence baselines, internal temperature, and capability profiles
based on dispatch outcomes and external feedback.
"""

from jarvis.court.emperor import CourtPhase, Decree, Emperor, ImperialCourt, CourtRecord
from jarvis.court.minister import (
    Edict,
    Memorial,
    Minister,
    MinisterProfile,
    MinisterState,
    ExperienceRecord,
)
from jarvis.court.ministers import create_ministers
from jarvis.court.diversity import (
    CatastropheReport,
    DiversityMonitor,
    DiversitySnapshot,
)
from jarvis.court.evolution import (
    AdaptiveRateConfig,
    CrossoverMode,
    EliteTurnoverMode,
    EvolutionAction,
    EvolutionEvent,
    EvolutionRateMode,
    EvolutionReport,
    MinisterGenome,
    MinisterStatus,
    SurvivalMechanism,
    TaskContext,
    TaskDifficulty,
)

__all__ = [
    "AdaptiveRateConfig",
    "CatastropheReport",
    "CourtPhase",
    "Decree",
    "DiversityMonitor",
    "DiversitySnapshot",
    "Edict",
    "EliteTurnoverMode",
    "Emperor",
    "EvolutionAction",
    "EvolutionEvent",
    "EvolutionRateMode",
    "EvolutionReport",
    "ImperialCourt",
    "CourtRecord",
    "CrossoverMode",
    "Memorial",
    "Minister",
    "MinisterGenome",
    "MinisterProfile",
    "MinisterState",
    "MinisterStatus",
    "ExperienceRecord",
    "SurvivalMechanism",
    "TaskContext",
    "TaskDifficulty",
    "create_ministers",
]
