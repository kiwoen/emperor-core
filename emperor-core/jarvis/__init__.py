"""
JARVIS — Just A Rather Very Intelligent System.

A multi-domain autonomous AI orchestrator inspired by the Iron Man J.A.R.V.I.S.
It thinks, learns, and acts across every domain of human life — not as a tool,
but as a digital extension of its user.

Architecture:
    Five-layer design: Control Plane → Execution Engine → Evolution Engine
    → Capability Layer → Persistence Layer

    Eight domain modules plug into a universal orchestration core.
    Each domain is self-contained, hot-pluggable, and autonomously evolveable.

    Core innovation: Self-Evolution Loop — the system not only executes
    tasks but learns from outcomes, optimizes its own prompts, selects the
    best models per task, and grows its capability tree over time.
"""

__version__ = "0.1.0"
__author__ = "JARVIS Core Team"

# 导入核心模块
from jarvis.pipeline import (
    ServicePipeline, Stage, StageStatus, PipelineStatus,
    PipelineRegistry, pipeline_registry,
)

# P0.1: Governance Agent — Agent-monitoring-Agent layer
from jarvis.governance_agent import (
    GovernanceAgent,
    GovernanceRule,
    GovernanceResult,
    GovernanceStatus,
    RulePriority,
)

# P0.2: Bounded Autonomy — three-zone action space framework
from jarvis.bounded_autonomy import (
    ActionZone,
    ActionSpace,
    BoundedAutonomyEngine,
    BoundedAutonomyResult,
)

# P2: Reflexion — self-reflection & auto-correction layer
from jarvis.reflexion import (
    ReflexionEngine,
    ReflectionResult,
    ReflectionIssue,
    CheckType,
    CorrectionStatus,
    create_reflexion_engine,
)

# P2.5: State Machine — LangGraph-inspired execution engine
from jarvis.state_machine import (
    State,
    Transition,
    StateMachine,
    StateMachineContext,
    create_dispatch_workflow,
    create_error_recovery_workflow,
    list_workflow_templates,
    execute_workflow,
)
