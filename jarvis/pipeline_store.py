"""In-memory pipeline execution records store.

Stores up to MAX_RECORDS pipeline execution entries, each containing
template/pipeline_id/status/steps/total_steps/elapsed_ms/created_at.
Ported from publish_pipeline() events into a queryable store for the
Dashboard Pipeline panel.
"""

from __future__ import annotations

import threading
import time
from typing import Optional


MAX_RECORDS = 200


class PipelineStore:
    """Thread-safe ring-buffer for pipeline execution records."""

    def __init__(self) -> None:
        self._records: list[dict] = []
        self._lock = threading.Lock()

    def add(
        self,
        template: str,
        pipeline_id: str,
        status: str,
        steps: int | None = None,
        total_steps: int | None = None,
        elapsed_ms: float = 0.0,
        step_details: list[dict] | None = None,
    ) -> dict:
        """Append a pipeline execution record and trim to MAX_RECORDS.

        Returns the newly-created record dict.
        """
        record = {
            "template": template,
            "pipeline_id": pipeline_id,
            "status": status,
            "steps": steps,
            "total_steps": total_steps if total_steps is not None else steps,
            "elapsed_ms": round(elapsed_ms, 1),
            "created_at": time.time(),
            "step_details": step_details or [],
        }
        with self._lock:
            self._records.append(record)
            # Keep only the most recent MAX_RECORDS
            if len(self._records) > MAX_RECORDS:
                self._records = self._records[-MAX_RECORDS:]
        return record

    def update(
        self,
        pipeline_id: str,
        status: str | None = None,
        steps: int | None = None,
        elapsed_ms: float | None = None,
        step_details: list[dict] | None = None,
    ) -> bool:
        """Update an existing pipeline record by pipeline_id.

        Returns True if the record was found and updated.
        """
        with self._lock:
            for r in self._records:
                if r["pipeline_id"] == pipeline_id:
                    if status is not None:
                        r["status"] = status
                    if steps is not None:
                        r["steps"] = steps
                    if elapsed_ms is not None:
                        r["elapsed_ms"] = round(elapsed_ms, 1)
                    if step_details is not None:
                        r["step_details"] = step_details
                    return True
        return False

    def get_recent(self, limit: int = 10, status: str | None = None) -> list[dict]:
        """Return the most recent pipeline records (newest first).

        Args:
            limit: Max records to return (clamped to 1-100).
            status: Optional filter by pipeline status (running/completed/failed).
        """
        limit = max(1, min(limit, 100))
        with self._lock:
            records = list(self._records)
        # Reverse for newest-first
        records.reverse()
        if status:
            records = [r for r in records if r["status"] == status]
        return records[:limit]

    def get_by_id(self, pipeline_id: str) -> Optional[dict]:
        """Return a single pipeline record by pipeline_id, or None."""
        with self._lock:
            for r in self._records:
                if r["pipeline_id"] == pipeline_id:
                    return dict(r)
        return None

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def clear(self) -> None:
        """Remove all stored records (mainly for tests)."""
        with self._lock:
            self._records.clear()


# Module-level singleton
pipeline_store = PipelineStore()
