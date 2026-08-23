"""
Self-Evolution Controller.

JARVIS does not just execute — it improves. This module implements
a closed-loop learning system where every task outcome feeds back
into the system's knowledge, prompts, and model selection.

Evolution Loop:
    Task → Execute → Observe Outcome → Analyze → Optimize → Repeat

Three Optimization Layers:
    L1: Prompt Optimization (TextGrad-style gradient descent on prompts)
    L2: Model Selection (A/B testing models per task type)
    L3: Capability Growth (proposing new domain modules or tools)

Key innovation: Unlike static prompt engineering, this system treats
prompts as learnable parameters optimized by outcome feedback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.evolution")


@dataclass
class TaskRecord:
    """A single task execution record for learning."""

    task_id: str
    intent_text: str
    domain: str
    action: str
    success: bool
    execution_time_ms: float
    tokens_consumed: int
    user_feedback: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    prompt_used: str = ""
    model_used: str = ""


@dataclass
class PromptVariant:
    """A versioned prompt template."""

    version: int
    text: str
    score: float = 0.0
    trials: int = 0
    success_rate: float = 0.0
    avg_execution_time: float = 0.0


class EvolutionController:
    """Manages the self-evolution lifecycle.

    Three concurrent processes:
    1. PromptOptimizer — gradient-free prompt optimization
    2. ModelSelector — per-task-type model routing
    3. CapabilityGrower — proposes new tools/domains based on unmet needs
    """

    def __init__(self, data_dir: Path, config: Any) -> None:
        self.data_dir = data_dir / "evolution"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.records: list[TaskRecord] = []
        self.prompt_variants: dict[str, list[PromptVariant]] = defaultdict(list)
        self.model_stats: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._load_state()

    # ── Computed Statistics Properties ────────────────────────────

    @property
    def total_cycles(self) -> int:
        """Total execution cycles recorded."""
        return len(self.records)

    @property
    def optimizations_applied(self) -> int:
        """Number of optimization records written."""
        return len(self._load_optimization_records())

    @property
    def model_switches(self) -> int:
        """Number of task types that have tried multiple models."""
        return sum(1 for models in self.model_stats.values() if len(models) > 1)

    @property
    def capability_additions(self) -> int:
        """Number of optimization events (proxy for capability growth)."""
        return len(self._load_optimization_records())

    @property
    def average_score(self) -> float:
        """Overall success rate across all records."""
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.success) / len(self.records)

    @property
    def history(self) -> list[dict[str, Any]]:
        """Recent task execution history."""
        return [
            {
                "task_id": r.task_id,
                "domain": r.domain,
                "success": r.success,
                "timestamp": r.timestamp,
            }
            for r in self.records[-50:]
        ]

    @property
    def most_improved_domain(self) -> str:
        """Domain with the most activity (proxy for improvement)."""
        if not self.records:
            return ""
        from collections import Counter

        domain_counts = Counter(r.domain for r in self.records)
        return domain_counts.most_common(1)[0][0] if domain_counts else ""

    async def record_success(self, intent: Any, result: Any) -> None:
        """Record a successful task for learning."""
        record = TaskRecord(
            task_id=hashlib.sha256(
                f"{intent.raw_text}{time.time()}".encode()
            ).hexdigest()[:12],
            intent_text=intent.raw_text,
            domain=intent.primary_domain.name,
            action=intent.action,
            success=result.success,
            execution_time_ms=result.execution_time_ms,
            tokens_consumed=result.tokens_consumed,
            model_used=getattr(result, "model_used", "unknown"),
        )
        self.records.append(record)

        # Update model stats
        task_key = f"{intent.primary_domain.name}:{intent.action}"
        self.model_stats[task_key][record.model_used].append(result.execution_time_ms)

        # Periodic optimization trigger
        if len(self.records) % 100 == 0:
            await self.optimize_prompts()
            await self.optimize_model_routing()

    async def optimize_prompts(self) -> None:
        """Optimize prompts based on historical task outcomes.

        Strategy: TextGrad-style iterative refinement.
        1. Identify low-performing task types
        2. Generate prompt variants (candidates)
        3. A/B test variants in production
        4. Promote winners, deprecate losers
        """
        # Group records by task type
        by_task: dict[str, list[TaskRecord]] = defaultdict(list)
        for record in self.records[-1000:]:
            key = f"{record.domain}:{record.action}"
            by_task[key].append(record)

        for task_key, task_records in by_task.items():
            if len(task_records) < 20:
                continue

            success_rate = sum(1 for r in task_records if r.success) / len(task_records)
            avg_time = sum(r.execution_time_ms for r in task_records) / len(task_records)

            if success_rate < 0.7:
                logger.info(
                    "Low success rate for %s: %.1f%% — optimizing prompt",
                    task_key,
                    success_rate * 100,
                )
                # In production, this would invoke a meta-LLM to refine prompts
                # For now, log the optimization intent
                self._store_optimization_record(task_key, success_rate, avg_time)

    async def optimize_model_routing(self) -> None:
        """Select the best model for each task type based on historical data.

        Criteria: Success rate, execution speed, cost (tokens), quality.
        """
        for task_key, model_data in self.model_stats.items():
            best_model = None
            best_score = float("inf")
            for model_name, times in model_data.items():
                if len(times) < 10:
                    continue
                avg_time = sum(times) / len(times)
                if avg_time < best_score:
                    best_score = avg_time
                    best_model = model_name

            if best_model:
                logger.debug(
                    "Best model for %s: %s (avg %.0fms)",
                    task_key, best_model, best_score,
                )
                # In production, this updates config.model.task_model_map

    async def propose_capabilities(self) -> list[str]:
        """Analyze unmet needs and propose new capabilities.

        Scans failed tasks, user feedback, and domain gaps to
        suggest new tools, domain modules, or integrations.
        """
        proposals: list[str] = []
        recent_failures = [r for r in self.records[-500:] if not r.success]
        if not recent_failures:
            return proposals

        failure_domains = defaultdict(int)
        for record in recent_failures:
            failure_domains[record.domain] += 1

        for domain, count in failure_domains.items():
            if count > 10:
                proposals.append(
                    f"Domain '{domain}' has {count} recent failures — "
                    "consider adding specialized tools or expanding capabilities"
                )

        return proposals

    def get_performance_report(self) -> dict[str, Any]:
        """Generate a system-wide performance and learning report."""
        if not self.records:
            return {"status": "No data yet"}

        total = len(self.records)
        successful = sum(1 for r in self.records if r.success)

        return {
            "total_tasks": total,
            "success_rate": f"{successful / total * 100:.1f}%",
            "avg_execution_time_ms": f"{sum(r.execution_time_ms for r in self.records) / total:.0f}",
            "domains": {
                d: sum(1 for r in self.records if r.domain == d)
                for d in set(r.domain for r in self.records)
            },
            "optimization_candidates": len(self._load_optimization_records()),
            "learning_rate": f"{sum(1 for r in self.records if r.timestamp > time.time() - 86400)} tasks/day",
        }

    def _store_optimization_record(self, task_key: str, rate: float, avg_time: float) -> None:
        file_path = self.data_dir / "optimization_log.jsonl"
        record = {
            "timestamp": datetime.now().isoformat(),
            "task_key": task_key,
            "success_rate": rate,
            "avg_execution_time_ms": avg_time,
        }
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _load_optimization_records(self) -> list[dict]:
        file_path = self.data_dir / "optimization_log.jsonl"
        if not file_path.exists():
            return []
        records = []
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        return records

    def _load_state(self) -> None:
        """Load persisted evolution state."""
        records_path = self.data_dir / "task_records.jsonl"
        if records_path.exists():
            with open(records_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            data = json.loads(line)
                            self.records.append(TaskRecord(**data))
                        except (json.JSONDecodeError, TypeError):
                            pass

    def save_state(self) -> None:
        """Persist evolution state to disk."""
        records_path = self.data_dir / "task_records.jsonl"
        with open(records_path, "w", encoding="utf-8") as f:
            for record in self.records[-10000:]:  # Keep last 10k records
                f.write(json.dumps({
                    "task_id": record.task_id,
                    "intent_text": record.intent_text,
                    "domain": record.domain,
                    "action": record.action,
                    "success": record.success,
                    "execution_time_ms": record.execution_time_ms,
                    "tokens_consumed": record.tokens_consumed,
                    "user_feedback": record.user_feedback,
                    "timestamp": record.timestamp,
                    "model_used": record.model_used,
                }, ensure_ascii=False) + "\n")
