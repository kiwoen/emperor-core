"""
Imperial Court System (朝堂系统) — multi-agent deliberation architecture.

The Court is a microcosm of a Chinese imperial court: the Sovereign (天子)
receives petitions (user intents), analyzes them, and dispatches edicts
to specialized Ministers (大臣). Each Minister is modeled after a real
world-class AI, embodying its specific strengths. Ministers deliberate
independently (parallel), submit memorials (reports), and the Sovereign
synthesizes the final decree.

For the one-stop convenience API, use :class:`Court` directly:

    from huanxin.court import Court
    court = Court()
    court.register("alpha", domain="math")
    court.register("beta",  domain="code")
    court.evolve(10)
    print(court.summary())
"""

from huanxin.court.sovereign import CourtPhase, Decree, Sovereign, ImperialCourt, CourtRecord
from huanxin.court.minister import (
    Edict, Memorial, Minister, MinisterProfile, MinisterState, ExperienceRecord,
)
from huanxin.court.ministers import create_ministers
from huanxin.court.diversity import (
    CatastropheReport, DiversityMonitor, DiversitySnapshot,
)
from huanxin.court.evolution import (
    AdaptiveRateConfig, CrossoverMode, EliteTurnoverMode,
    EvolutionAction, EvolutionEvent, EvolutionRateMode, EvolutionReport,
    MinisterGenome, MinisterStatus, SurvivalMechanism,
    TaskContext, TaskDifficulty,
)
from huanxin.court.court import Court, CourtConfig
from huanxin.court.history import CycleRecord, EvolutionHistory

__all__ = [
    "AdaptiveRateConfig",
    "CatastropheReport",
    "Court", "CourtConfig",
    "CourtPhase", "CourtRecord",
    "CrossoverMode", "CycleRecord",
    "Decree", "DiversityMonitor", "DiversitySnapshot",
    "Edict", "EliteTurnoverMode", "Sovereign",
    "EvolutionAction", "EvolutionEvent", "EvolutionHistory",
    "EvolutionRateMode", "EvolutionReport",
    "ImperialCourt",
    "Memorial", "Minister", "MinisterGenome",
    "MinisterProfile", "MinisterState", "MinisterStatus",
    "ExperienceRecord",
    "SurvivalMechanism",
    "TaskContext", "TaskDifficulty",
    "create_ministers",
]
