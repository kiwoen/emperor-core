"""
JARVIS Cross-Domain Workflow Engine.

Orchestrates multi-domain pipelines where the output of one domain
feeds as context into the next. Supports pipeline (sequential) and
parallel (fan-out) execution modes.

Core concept: treat domains as composable units — a RESEARCH → FINANCE
pipeline produces an investment report, a CREATOR → ENGINEERING pipeline
generates code from a design spec.

Architecture:
    ┌──────────────────────────────────────────────┐
    │            WORKFLOW ENGINE                    │
    │                                               │
    │  Intent → Step Planner → Executor → Merger   │
    │                                               │
    │  pipeline:  A → B → C  (context chains)      │
    │  parallel:  A | B | C → merge                │
    └──────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

from jarvis.core.orchestrator import Domain, Intent, TaskResult

logger = logging.getLogger("jarvis.workflow")


class WorkflowMode(Enum):
    PIPELINE = auto()   # sequential, each step feeds next
    PARALLEL = auto()   # fan-out, all run concurrently
    HYBRID = auto()     # pipeline with parallel sub-steps


@dataclass
class WorkflowStep:
    """A single step in a cross-domain workflow."""

    domain: Domain
    prompt: str  # transformed or contextualized prompt for this step
    depends_on: list[int] = field(default_factory=list)  # indices of prerequisite steps
    role: str = ""  # descriptive label like "research", "analysis", "generation"


@dataclass
class WorkflowResult:
    """Aggregated result of a multi-domain workflow."""

    success: bool
    mode: WorkflowMode
    steps: list[tuple[Domain, TaskResult]] = field(default_factory=list)
    merged_output: str = ""
    error: Optional[str] = None
    total_execution_ms: float = 0.0

    @property
    def all_succeeded(self) -> bool:
        return all(r.success for _, r in self.steps)


class WorkflowEngine:
    """Executes cross-domain workflows.

    Given an intent with primary + secondary domains, plans and
    executes a workflow where domain outputs chain together.

    In the future this will support more complex topologies (DAGs),
    but for now it handles pipeline and parallel patterns.
    """

    # Common cross-domain patterns for auto-planning
    DOMAIN_TRANSITIONS: dict[tuple[Domain, Domain], str] = {
        # Research → downstream
        (Domain.RESEARCH, Domain.FINANCE): "research_to_investment",
        (Domain.RESEARCH, Domain.ENGINEERING): "research_to_implementation",
        (Domain.RESEARCH, Domain.CREATOR): "research_to_content",
        # Engineering → downstream
        (Domain.ENGINEERING, Domain.SECURITY): "code_to_security_audit",
        (Domain.ENGINEERING, Domain.CREATOR): "code_to_documentation",
        # Creator → downstream
        (Domain.CREATOR, Domain.ENGINEERING): "design_to_code",
        (Domain.CREATOR, Domain.RESEARCH): "content_to_research",
        # Health/Personal crossover
        (Domain.HEALTH, Domain.PERSONAL): "health_to_planning",
        (Domain.PERSONAL, Domain.HEALTH): "planning_to_health",
        # Finance crossover
        (Domain.FINANCE, Domain.PERSONAL): "finance_to_budget",
        # Home crossover
        (Domain.HOME, Domain.SECURITY): "home_to_security",
    }

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator
        self.registry = orchestrator.registry

    def plan(self, intent: Intent) -> list[WorkflowStep]:
        """Auto-plan a workflow from an intent.

        Strategy:
        1. Primary domain gets the original prompt
        2. Each secondary domain gets a contextualized prompt
           that includes the primary domain's role
        3. Steps are ordered by domain dependency heuristics
        """
        domains = [intent.primary_domain] + [
            d for d in intent.secondary_domains if d != intent.primary_domain
        ]
        domains = list(dict.fromkeys(domains))  # dedupe

        if len(domains) <= 1:
            # Single domain — no workflow needed
            return []

        steps: list[WorkflowStep] = []
        for i, domain in enumerate(domains):
            role = self._domain_role(domain)
            if i == 0:
                prompt = intent.raw_text
                deps = []
            else:
                # Contextualize: tell the domain what the previous step produced
                prev_domain = domains[i - 1]
                transition = self.DOMAIN_TRANSITIONS.get((prev_domain, domain), "general")
                prompt = self._build_transition_prompt(
                    intent.raw_text, role, transition, prev_domain
                )
                deps = [i - 1]

            steps.append(WorkflowStep(
                domain=domain,
                prompt=prompt,
                depends_on=deps,
                role=role,
            ))

        return steps

    def detect_mode(self, intent: Intent) -> WorkflowMode:
        """Detect the execution mode from intent structure.

        - Parallel markers: "同时", "一边...一边...", "and also", "separately"
        - Hybrid markers: "先...然后同时..."
        - Default: pipeline
        """
        text = intent.raw_text.lower()
        parallel_markers = ["同时", "一起", "并行", "parallel", "concurrent",
                          "一边", "分别", "separately"]
        if any(m in text for m in parallel_markers):
            return WorkflowMode.PARALLEL
        return WorkflowMode.PIPELINE

    async def execute(self, intent: Intent) -> WorkflowResult:
        """Execute a cross-domain workflow.

        Handles three modes:
        - PIPELINE: sequential, each step receives previous output as context
        - PARALLEL: all steps run concurrently, then merge results
        - HYBRID: pipeline with parallel sub-steps (reserved for future)
        """
        steps = self.plan(intent)
        if not steps:
            return WorkflowResult(
                success=False,
                mode=WorkflowMode.PIPELINE,
                error="No cross-domain workflow detected (single domain only)",
            )

        mode = self.detect_mode(intent)
        logger.info("Workflow: %s mode, %d steps: %s",
                     mode.name, len(steps), [s.domain.name for s in steps])

        start = asyncio.get_event_loop().time()

        if mode == WorkflowMode.PIPELINE:
            step_results = await self._execute_pipeline(steps)
        elif mode == WorkflowMode.PARALLEL:
            step_results = await self._execute_parallel(steps)
        else:
            step_results = await self._execute_pipeline(steps)

        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        merged = self._merge_outputs(step_results, mode)

        return WorkflowResult(
            success=all(r.success for _, r in step_results),
            mode=mode,
            steps=step_results,
            merged_output=merged,
            total_execution_ms=elapsed,
        )

    async def _execute_pipeline(self, steps: list[WorkflowStep]) -> list[tuple[Domain, TaskResult]]:
        """Execute steps sequentially, chaining context."""
        results: list[tuple[Domain, TaskResult]] = []
        previous_output = ""

        for step in steps:
            # Create a sub-intent with accumulated context
            sub_intent = Intent(
                raw_text=step.prompt,
                primary_domain=step.domain,
            )

            # If we have previous output, inject it as context
            if previous_output and step.depends_on:
                context_text = f"Previous analysis result:\n{previous_output}\n\nNow, based on this: {step.prompt}"
                sub_intent.raw_text = context_text
                # Store previous result data for the handler
                sub_intent.entities["_workflow_context"] = previous_output

            # Execute via orchestrator (which will route to the right domain)
            module = self.registry.get(step.domain)
            if module is None:
                results.append((step.domain, TaskResult(
                    domain=step.domain,
                    success=False,
                    error=f"Domain {step.domain.name} not loaded",
                )))
                continue

            try:
                result = await module.handle(sub_intent)
                results.append((step.domain, result))
                if result.success:
                    previous_output = str(result.output) if result.output else ""
            except Exception as e:
                logger.exception("Workflow step failed: %s", step.domain.name)
                results.append((step.domain, TaskResult(
                    domain=step.domain,
                    success=False,
                    error=str(e),
                )))

        return results

    async def _execute_parallel(self, steps: list[WorkflowStep]) -> list[tuple[Domain, TaskResult]]:
        """Execute all steps concurrently."""
        async def run_step(step: WorkflowStep) -> tuple[Domain, TaskResult]:
            sub_intent = Intent(raw_text=step.prompt, primary_domain=step.domain)
            module = self.registry.get(step.domain)
            if module is None:
                return (step.domain, TaskResult(
                    domain=step.domain, success=False,
                    error=f"Domain {step.domain.name} not loaded"))
            try:
                result = await module.handle(sub_intent)
                return (step.domain, result)
            except Exception as e:
                return (step.domain, TaskResult(
                    domain=step.domain, success=False, error=str(e)))

        return list(await asyncio.gather(*(run_step(s) for s in steps)))

    def _merge_outputs(
        self,
        results: list[tuple[Domain, TaskResult]],
        mode: WorkflowMode,
    ) -> str:
        """Merge outputs from multiple domain steps into a coherent result."""
        if not results:
            return ""

        successful = [(d, r) for d, r in results if r.success]

        if mode == WorkflowMode.PIPELINE:
            # Pipeline: the last step's output is the primary result,
            # but include a summary of all steps
            parts = []
            for i, (domain, result) in enumerate(successful):
                label = self._domain_role(domain)
                parts.append(f"[Step {i+1}: {label} ({domain.name})]\n{result.output}")
            return "\n\n".join(parts)

        elif mode == WorkflowMode.PARALLEL:
            # Parallel: present all results side by side
            parts = ["=== Multi-Domain Analysis ==="]
            for domain, result in successful:
                parts.append(f"\n--- {domain.name} ({self._domain_role(domain)}) ---\n{result.output}")
            return "\n".join(parts)

        return "\n\n".join(str(r.output) for _, r in successful)

    def _domain_role(self, domain: Domain) -> str:
        roles = {
            Domain.PERSONAL: "Personal Assistant",
            Domain.RESEARCH: "Research",
            Domain.ENGINEERING: "Engineering",
            Domain.CREATOR: "Content Creation",
            Domain.SECURITY: "Security Analysis",
            Domain.HEALTH: "Health & Wellness",
            Domain.FINANCE: "Financial Analysis",
            Domain.HOME: "Smart Home",
            Domain.CORE: "System",
        }
        return roles.get(domain, "Assistant")

    def _build_transition_prompt(
        self,
        original: str,
        role: str,
        transition: str,
        prev_domain: Domain,
    ) -> str:
        """Build a contextualized prompt for a downstream step."""
        prev_role = self._domain_role(prev_domain)

        transition_guidance = {
            "research_to_investment": "使用上述研究结论，进行金融市场分析和投资建议",
            "research_to_implementation": "基于上述研究发现，生成技术实现方案",
            "research_to_content": "基于上述研究结果，创作面向大众的内容",
            "code_to_security_audit": "对上述代码进行安全审计，识别潜在漏洞",
            "code_to_documentation": "为上述代码生成技术文档",
            "design_to_code": "根据上述设计方案，生成实现代码",
            "content_to_research": "基于上述内容，深入调研相关技术/学术背景",
            "health_to_planning": "根据上述健康数据，制定个人日程和计划",
            "planning_to_health": "基于上述计划安排，给出健康建议",
            "finance_to_budget": "基于上述财务分析，制定个人预算计划",
            "home_to_security": "对上述智能家居配置进行安全评估",
        }

        guidance = transition_guidance.get(transition,
            f"基于{prev_role}的分析结果，从{role}的角度给出建议")

        return f"原始需求：{original}\n\n你的角色：{role}\n任务：{guidance}"

    # ------------------------------------------------------------------
    # Heuristic: detect whether a given intent warrants multi-domain
    # ------------------------------------------------------------------

    CROSS_DOMAIN_PATTERNS: list[tuple[str, list[Domain]]] = [
        # Chinese patterns
        ("投资" + "研究" if False else "", []),  # placeholder for below
    ]

    @staticmethod
    def requires_workflow(intent: Intent) -> bool:
        """Heuristic: does this intent benefit from multi-domain execution?"""
        if len(intent.secondary_domains) >= 2:
            return True
        if intent.action in ("analyze", "generate", "比较", "对比", "分析", "生成"):
            return len(intent.secondary_domains) >= 1
        # Check for cross-domain keywords
        text = intent.raw_text.lower()
        cross_keywords = [
            "并且", "然后", "之后", "基于以上", "根据结果",
            "and then", "based on", "根据", "综合",
        ]
        return any(kw in text for kw in cross_keywords)
