"""
Distillation trace store.

Captures how different LLMs answer the *same* prompt -- the raw material for
self-evolution / knowledge distillation. Traces are produced ONLY by genuine
model calls (see :class:`jarvis.multi_model_executor.RealLLMExecutor`); the
offline mock executor never writes here, so the corpus stays honest.

Persistence is optional: in-memory by default, with an append-only JSONL sink
for durable corpus building.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from typing import Optional

logger = logging.getLogger("jarvis.learning.distillation_store")


@dataclass
class DistillationTrace:
    """One recorded attempt at answering a prompt with a specific model."""

    ts: float  # epoch seconds
    prompt: str
    model_id: str
    tier: str
    output: str
    latency_ms: float
    cost_estimate: float
    success: bool
    error: str = ""


class DistillationStore:
    """In-memory + optional JSONL corpus of distillation traces."""

    def __init__(self, append_jsonl_path: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._traces: list[DistillationTrace] = []
        self.append_jsonl_path = append_jsonl_path
        if append_jsonl_path:
            parent = os.path.dirname(os.path.abspath(append_jsonl_path))
            if parent:
                os.makedirs(parent, exist_ok=True)

    def record(self, trace: DistillationTrace) -> None:
        """Append a trace to memory and (optionally) the JSONL sink."""
        with self._lock:
            self._traces.append(trace)
            if self.append_jsonl_path:
                try:
                    with open(self.append_jsonl_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(asdict(trace), ensure_ascii=False) + "\n")
                except Exception:  # pragma: no cover - persistence is best-effort
                    logger.warning(
                        "Failed to append distillation trace to %s",
                        self.append_jsonl_path,
                        exc_info=True,
                    )

    def all(self) -> list[DistillationTrace]:
        """Return a snapshot of all recorded traces."""
        with self._lock:
            return list(self._traces)

    def __len__(self) -> int:
        return len(self._traces)
