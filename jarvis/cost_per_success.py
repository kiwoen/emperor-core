"""
JARVIS Cost-per-Successful-Run Tracker.

Tracks per-task outcomes (success/failure) alongside their token-cost data,
computing cost-efficiency metrics: cost-per-successful-run, success_rate,
avg_tokens_per_run, and time-bucketed cost trends.

Also supports CostEfficiencyAlert: auto-alerts when cost-per-successful-run
exceeds 2× the configured baseline.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.cost_per_success")


# ══════════════════════════════════════════════════════════════════
# TaskOutcomeRecord
# ══════════════════════════════════════════════════════════════════

@dataclass
class TaskOutcomeRecord:
    """A single task execution outcome with associated cost data."""
    timestamp: float           # Unix timestamp (seconds)
    task_id: str               # Task identifier
    success: bool              # Whether the task completed successfully
    cost_usd: float            # Total USD cost for this task (sum of model calls)
    tokens_in: int             # Total input tokens consumed
    tokens_out: int            # Total output tokens consumed
    execution_time_ms: float   # Execution time in milliseconds
    domain: str = ""           # Task domain
    model_calls: int = 0       # Number of model invocations for this task


# ══════════════════════════════════════════════════════════════════
# CostEfficiencyAlert
# ══════════════════════════════════════════════════════════════════

class CostEfficiencyAlert:
    """Alert raised when cost-per-successful-run exceeds 2× baseline."""

    def __init__(
        self,
        current_cpsr: float,
        baseline_cpsr: float,
        ratio: float,
        timestamp: float,
    ) -> None:
        self.current_cpsr = current_cpsr
        self.baseline_cpsr = baseline_cpsr
        self.ratio = ratio
        self.timestamp = timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_cost_per_success": round(self.current_cpsr, 8),
            "baseline_cost_per_success": round(self.baseline_cpsr, 8),
            "ratio": round(self.ratio, 4),
            "severity": "warning" if self.ratio < 3 else "critical",
            "timestamp_iso": datetime.fromtimestamp(self.timestamp).isoformat(),
        }


# ══════════════════════════════════════════════════════════════════
# CostPerSuccessTracker
# ══════════════════════════════════════════════════════════════════

class CostPerSuccessTracker:
    """Tracks task outcomes for cost-efficiency metrics.

    Usage::

        tracker = CostPerSuccessTracker(baseline_cost_per_success=0.05)
        tracker.record(task_id="abc", success=True, cost_usd=0.01,
                       tokens_in=500, tokens_out=200, execution_time_ms=350)
        report = tracker.get_report()
    """

    def __init__(
        self,
        baseline_cost_per_success: float = 0.05,
        max_records: int = 5000,
        persistence_path: str = "",
    ) -> None:
        self._records: list[TaskOutcomeRecord] = []
        self._lock = threading.Lock()
        self._baseline = baseline_cost_per_success
        self._max_records = max_records
        self._persistence_path = persistence_path
        self._last_alert: Optional[CostEfficiencyAlert] = None

        if self._persistence_path:
            self._load_from_disk()

    # ── Persistence ───────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        path = Path(self._persistence_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records = []
            for r in data.get("records", []):
                records.append(TaskOutcomeRecord(
                    timestamp=r["timestamp"],
                    task_id=r.get("task_id", ""),
                    success=r["success"],
                    cost_usd=r["cost_usd"],
                    tokens_in=r["tokens_in"],
                    tokens_out=r["tokens_out"],
                    execution_time_ms=r.get("execution_time_ms", 0.0),
                    domain=r.get("domain", ""),
                    model_calls=r.get("model_calls", 0),
                ))
            with self._lock:
                self._records = records
            logger.info("Loaded %d outcome records from %s", len(records), path)
        except Exception:
            logger.warning("Failed to load outcome records", exc_info=True)

    def _save_to_disk(self) -> None:
        if not self._persistence_path:
            return
        path = Path(self._persistence_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {
                    "updated_at": datetime.now().isoformat(),
                    "count": len(self._records),
                    "records": [
                        {
                            "timestamp": r.timestamp,
                            "task_id": r.task_id,
                            "success": r.success,
                            "cost_usd": r.cost_usd,
                            "tokens_in": r.tokens_in,
                            "tokens_out": r.tokens_out,
                            "execution_time_ms": r.execution_time_ms,
                            "domain": r.domain,
                            "model_calls": r.model_calls,
                        }
                        for r in self._records
                    ],
                }
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        except Exception:
            logger.warning("Failed to save outcome records", exc_info=True)

    # ── Record ────────────────────────────────────────────────────

    def record(
        self,
        task_id: str,
        success: bool,
        cost_usd: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
        execution_time_ms: float = 0.0,
        domain: str = "",
        model_calls: int = 0,
    ) -> TaskOutcomeRecord:
        """Record a task outcome and check for cost-efficiency alerts.

        Returns:
            The newly created TaskOutcomeRecord.
        """
        rec = TaskOutcomeRecord(
            timestamp=time.time(),
            task_id=task_id,
            success=success,
            cost_usd=cost_usd,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            execution_time_ms=execution_time_ms,
            domain=domain,
            model_calls=model_calls,
        )

        with self._lock:
            self._records.append(rec)
            # Trim oldest records if exceeding max
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]

        # Persist
        if self._persistence_path:
            try:
                self._save_to_disk()
            except Exception:
                pass

        # Check alert
        alert = self._check_alert()
        if alert:
            logger.warning(
                "CostEfficiencyAlert! CPSR=%.6f baseline=%.6f ratio=%.2f",
                alert.current_cpsr, alert.baseline_cpsr, alert.ratio,
            )
            self._last_alert = alert

        return rec

    # ── Metrics ───────────────────────────────────────────────────

    def _snapshot(self) -> list[TaskOutcomeRecord]:
        with self._lock:
            return list(self._records)

    def _window(self, hours: int) -> list[TaskOutcomeRecord]:
        """Records within the last N hours."""
        cutoff = time.time() - hours * 3600
        return [r for r in self._snapshot() if r.timestamp >= cutoff]

    def total_tasks(self, hours: int = 0) -> int:
        """Total number of tasks recorded (optionally in last N hours)."""
        records = self._snapshot() if hours == 0 else self._window(hours)
        return len(records)

    def success_count(self, hours: int = 0) -> int:
        """Number of successful tasks."""
        records = self._snapshot() if hours == 0 else self._window(hours)
        return sum(1 for r in records if r.success)

    def fail_count(self, hours: int = 0) -> int:
        """Number of failed tasks."""
        records = self._snapshot() if hours == 0 else self._window(hours)
        return sum(1 for r in records if not r.success)

    def success_rate(self, hours: int = 0) -> float:
        """Success rate as a fraction [0, 1]."""
        total = self.total_tasks(hours)
        if total == 0:
            return 1.0
        return self.success_count(hours) / total

    def total_cost(self, hours: int = 0) -> float:
        """Total USD cost for all tasks."""
        records = self._snapshot() if hours == 0 else self._window(hours)
        return round(sum(r.cost_usd for r in records), 8)

    def cost_per_successful_run(self, hours: int = 0) -> float:
        """Total cost / number of successful tasks."""
        success = self.success_count(hours)
        if success == 0:
            return 0.0
        return round(self.total_cost(hours) / success, 8)

    def avg_tokens_per_run(self, hours: int = 0) -> dict[str, int]:
        """Average input/output tokens per task."""
        records = self._snapshot() if hours == 0 else self._window(hours)
        total = len(records)
        if total == 0:
            return {"avg_tokens_in": 0, "avg_tokens_out": 0}
        return {
            "avg_tokens_in": sum(r.tokens_in for r in records) // total,
            "avg_tokens_out": sum(r.tokens_out for r in records) // total,
        }

    def cost_trend(self, bucket: str = "day", hours: int = 168) -> list[dict[str, Any]]:
        """Cost trend aggregated by day or hour.

        Args:
            bucket: "day" or "hour".
            hours: Lookback window in hours (default 168 = 7 days).

        Returns:
            List of {label, cost_usd, total_tasks, successful_tasks,
                      cost_per_success, success_rate}.
        """
        cutoff = time.time() - hours * 3600
        records = [r for r in self._snapshot() if r.timestamp >= cutoff]
        if not records:
            return []

        buckets: dict[str, dict[str, Any]] = {}
        for r in records:
            dt = datetime.fromtimestamp(r.timestamp)
            if bucket == "hour":
                label = dt.strftime("%Y-%m-%dT%H:00")
            else:
                label = dt.strftime("%Y-%m-%d")
            if label not in buckets:
                buckets[label] = {
                    "cost_usd": 0.0,
                    "total_tasks": 0,
                    "successful_tasks": 0,
                }
            b = buckets[label]
            b["cost_usd"] += r.cost_usd
            b["total_tasks"] += 1
            if r.success:
                b["successful_tasks"] += 1

        trend = []
        for label, b in sorted(buckets.items()):
            s = b["successful_tasks"] if b["successful_tasks"] > 0 else 1
            trend.append({
                "label": label,
                "cost_usd": round(b["cost_usd"], 8),
                "total_tasks": b["total_tasks"],
                "successful_tasks": b["successful_tasks"],
                "cost_per_success": round(b["cost_usd"] / s, 8),
                "success_rate": round(
                    b["successful_tasks"] / b["total_tasks"], 4
                ) if b["total_tasks"] > 0 else 0.0,
            })

        return trend

    # ── Alert ─────────────────────────────────────────────────────

    def _check_alert(self) -> Optional[CostEfficiencyAlert]:
        """Check if cost-per-successful-run exceeds 2× baseline."""
        total_tasks = self.total_tasks()
        if total_tasks < 5:
            # Need at least 5 records to trigger a meaningful alert
            return None
        cpsr = self.cost_per_successful_run()
        if cpsr <= 0:
            return None
        ratio = cpsr / self._baseline
        if ratio >= 2.0:
            return CostEfficiencyAlert(
                current_cpsr=cpsr,
                baseline_cpsr=self._baseline,
                ratio=ratio,
                timestamp=time.time(),
            )
        return None

    @property
    def last_alert(self) -> Optional[CostEfficiencyAlert]:
        with self._lock:
            return self._last_alert

    # ── Report ────────────────────────────────────────────────────

    def get_report(
        self,
        format: str = "json",
        hours: int = 0,
        trend_bucket: str = "day",
    ) -> dict[str, Any]:
        """Return a comprehensive cost-efficiency report.

        Args:
            format: "json" or "markdown".
            hours: Analysis window in hours (0 = all-time).
            trend_bucket: "day" or "hour" for cost_trend buckets.

        Returns:
            Dict with metric keys, or if format="markdown", a dict with
            "markdown" key containing the Markdown string.
        """
        data = {
            "window_hours": hours if hours > 0 else "all_time",
            "total_tasks": self.total_tasks(hours),
            "successful_tasks": self.success_count(hours),
            "failed_tasks": self.fail_count(hours),
            "success_rate": round(self.success_rate(hours), 4),
            "total_cost_usd": self.total_cost(hours),
            "cost_per_successful_run": self.cost_per_successful_run(hours),
            **self.avg_tokens_per_run(hours),
            "baseline_cpsr": self._baseline,
        }

        cpsr = data["cost_per_successful_run"]
        if cpsr > 0:
            data["deviation_from_baseline"] = round(
                (cpsr - self._baseline) / self._baseline, 4
            )
        else:
            data["deviation_from_baseline"] = -1.0

        # Trend
        trend_hours = hours if hours > 0 else 168
        data["cost_trend"] = self.cost_trend(bucket=trend_bucket, hours=trend_hours)

        # Latest alert
        alert = self.last_alert
        data["active_alert"] = alert.to_dict() if alert else None

        if format == "markdown":
            md = self._to_markdown(data)
            data["markdown"] = md

        return data

    @staticmethod
    def _to_markdown(data: dict[str, Any]) -> str:
        """Convert report data to Markdown string."""
        lines = [
            "# Cost Efficiency Report",
            "",
            f"**Window:** {data['window_hours']}",
            f"**Total Tasks:** {data['total_tasks']}",
            f"**Successful Tasks:** {data['successful_tasks']}",
            f"**Failed Tasks:** {data['failed_tasks']}",
            f"**Success Rate:** {data['success_rate']:.2%}",
            "",
            "## Cost Metrics",
            "",
            f"- **Total Cost:** ${data['total_cost_usd']:.6f}",
            f"- **Cost per Successful Run:** ${data['cost_per_successful_run']:.6f}",
            f"- **Baseline CPSR:** ${data['baseline_cpsr']:.6f}",
        ]

        dev = data.get("deviation_from_baseline", 0)
        if dev > 0:
            lines.append(
                f"- **Deviation from Baseline:** +{dev:.2%} "
                f"(⚠️ exceeds baseline)"
            )
        elif dev > -1:
            lines.append(f"- **Deviation from Baseline:** {dev:.2%}")

        lines += [
            "",
            "## Token Efficiency",
            "",
            f"- **Avg Input Tokens/Run:** {data['avg_tokens_in']}",
            f"- **Avg Output Tokens/Run:** {data['avg_tokens_out']}",
        ]

        if data.get("active_alert"):
            a = data["active_alert"]
            lines += [
                "",
                "## ⚠️ Active Alert",
                "",
                f"- **Current CPSR:** ${a['current_cost_per_success']:.6f}",
                f"- **Baseline CPSR:** ${a['baseline_cost_per_success']:.6f}",
                f"- **Ratio:** {a['ratio']:.2f}× baseline",
                f"- **Severity:** {a['severity']}",
            ]

        return "\n".join(lines)

    # ── Reset ─────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all records (useful for testing)."""
        with self._lock:
            self._records.clear()
            self._last_alert = None
