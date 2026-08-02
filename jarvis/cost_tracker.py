"""
JARVIS Cost Tracker — per-invocation cost recording and reporting.

Tracks token usage and USD cost for every model call, enabling
daily/monthly budget visibility and per-model cost breakdowns.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.cost_tracker")


# ══════════════════════════════════════════════════════════════════
# CostRecord
# ══════════════════════════════════════════════════════════════════

@dataclass
class CostRecord:
    """A single cost-tracking record for one model invocation."""
    timestamp: float          # Unix timestamp (seconds)
    model_name: str           # e.g. "gpt-4o-mini"
    tokens_in: int            # Input / prompt tokens
    tokens_out: int           # Output / completion tokens
    cost_usd: float           # Total USD cost for this invocation
    task_id: str = ""         # Associated task / request ID
    operation: str = ""       # e.g. "invoke", "parallel", "ensemble"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp_iso"] = datetime.fromtimestamp(self.timestamp).isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CostRecord:
        d = dict(d)  # copy
        d.pop("timestamp_iso", None)
        return cls(**d)


# ══════════════════════════════════════════════════════════════════
# CostTracker
# ══════════════════════════════════════════════════════════════════

class CostTracker:
    """In-memory cost tracker with optional JSON-file persistence.

    Usage::

        tracker = CostTracker()
        tracker.record("gpt-4o", tokens_in=500, tokens_out=200)
        print(tracker.daily_total())
        print(tracker.per_model_breakdown())
    """

    def __init__(self, persistence_path: str = "") -> None:
        self._records: list[CostRecord] = []
        self._lock = threading.Lock()
        self._persistence_path = persistence_path

        # Load existing records from disk if path is provided
        if self._persistence_path:
            self._load_from_disk()
            logger.info("CostTracker persistence → %s", self._persistence_path)

    # ── Persistence ───────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        path = Path(self._persistence_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records = data if isinstance(data, list) else data.get("records", [])
            with self._lock:
                self._records = [CostRecord.from_dict(r) for r in records]
            logger.info("Loaded %d cost records from %s", len(records), path)
        except Exception:
            logger.warning("Failed to load cost records from %s", path, exc_info=True)

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
                    "records": [r.to_dict() for r in self._records],
                }
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.warning("Failed to save cost records to %s", path, exc_info=True)

    # ── Record ────────────────────────────────────────────────────

    def record(
        self,
        model_name: str,
        tokens_in: int,
        tokens_out: int,
        task_id: str = "",
        operation: str = "invoke",
    ) -> CostRecord:
        """Record a model invocation and return the CostRecord.

        The cost is computed from the MultiModelRouter registry if available,
        otherwise estimated at $0.002/1K tokens (a reasonable default).

        Args:
            model_name: e.g. "gpt-4o-mini", "deepseek-chat".
            tokens_in: Number of input / prompt tokens.
            tokens_out: Number of output / completion tokens.
            task_id: Optional task / request identifier.
            operation: e.g. "invoke", "parallel", "ensemble".

        Returns:
            The newly created CostRecord.
        """
        # Compute cost — use MultiModelRouter registry if available
        cost = self._compute_cost(model_name, tokens_in, tokens_out)

        record = CostRecord(
            timestamp=time.time(),
            model_name=model_name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=round(cost, 8),
            task_id=task_id,
            operation=operation,
        )

        with self._lock:
            self._records.append(record)

        # Persist asynchronously (best-effort)
        if self._persistence_path:
            try:
                self._save_to_disk()
            except Exception:
                pass

        return record

    @staticmethod
    def _compute_cost(model_name: str, tokens_in: int, tokens_out: int) -> float:
        """Compute cost from MultiModelRouter registry if available."""
        try:
            from jarvis.multi_model import MultiModelRouter
            dummy = MultiModelRouter.__new__(MultiModelRouter)
            # Access the default registry to find pricing
            from jarvis.multi_model import _DEFAULT_MODELS
            cfg = _DEFAULT_MODELS.get(model_name)
            if cfg:
                return (
                    (tokens_in / 1000) * cfg.cost_per_1k_input
                    + (tokens_out / 1000) * cfg.cost_per_1k_output
                )
        except Exception:
            pass
        # Fallback: $0.002 per 1K tokens
        return (tokens_in + tokens_out) / 1000 * 0.002

    # ── Query ─────────────────────────────────────────────────────

    def _records_snapshot(self) -> list[CostRecord]:
        with self._lock:
            return list(self._records)

    def _today_start(self) -> float:
        return datetime.combine(date.today(), datetime.min.time()).timestamp()

    def _month_start(self) -> float:
        today = date.today()
        return datetime.combine(today.replace(day=1), datetime.min.time()).timestamp()

    def daily_total(self) -> float:
        """Total USD cost for today."""
        cutoff = self._today_start()
        total = sum(
            r.cost_usd for r in self._records_snapshot() if r.timestamp >= cutoff
        )
        return round(total, 6)

    def monthly_total(self) -> float:
        """Total USD cost for the current month."""
        cutoff = self._month_start()
        total = sum(
            r.cost_usd for r in self._records_snapshot() if r.timestamp >= cutoff
        )
        return round(total, 6)

    def all_time_total(self) -> float:
        """Total USD cost since tracker was created."""
        total = sum(r.cost_usd for r in self._records_snapshot())
        return round(total, 6)

    def per_model_breakdown(self, since: float = 0.0) -> dict[str, dict[str, Any]]:
        """Cost breakdown per model: total cost, call count, total tokens.

        Args:
            since: Unix timestamp; only include records after this time.

        Returns:
            Dict of model_name → {cost_usd, calls, tokens_in, tokens_out}.
        """
        records = self._records_snapshot()
        if since > 0:
            records = [r for r in records if r.timestamp >= since]

        breakdown: dict[str, dict[str, Any]] = {}
        for r in records:
            if r.model_name not in breakdown:
                breakdown[r.model_name] = {
                    "cost_usd": 0.0,
                    "calls": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                }
            b = breakdown[r.model_name]
            b["cost_usd"] += r.cost_usd
            b["calls"] += 1
            b["tokens_in"] += r.tokens_in
            b["tokens_out"] += r.tokens_out

        # Round costs
        for v in breakdown.values():
            v["cost_usd"] = round(v["cost_usd"], 6)

        return breakdown

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent N cost records (newest first)."""
        records = self._records_snapshot()
        return [r.to_dict() for r in sorted(records, key=lambda r: r.timestamp, reverse=True)[:limit]]

    def summary(self) -> dict[str, Any]:
        """Return a comprehensive summary for API responses."""
        daily = self.daily_total()
        monthly = self.monthly_total()
        all_time = self.all_time_total()
        model_breakdown_daily = self.per_model_breakdown(since=self._today_start())
        model_breakdown_monthly = self.per_model_breakdown(since=self._month_start())

        return {
            "today_usd": daily,
            "this_month_usd": monthly,
            "all_time_usd": all_time,
            "total_calls": len(self._records_snapshot()),
            "by_model_today": model_breakdown_daily,
            "by_model_month": model_breakdown_monthly,
        }

    def reset(self) -> None:
        """Clear all records (useful for testing)."""
        with self._lock:
            self._records.clear()
