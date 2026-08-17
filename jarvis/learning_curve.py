"""Persistent learning-curve time series for the self-evolution loop.

Each evolution round appends one record (avg_merit / success_rate /
active_ministers + a per-minister snapshot) to a JSON store that lives on the
persistent data volume (``EMPEROR_DATA_DIR``, default ``/app/data``). Because it
resides on the named volume, the curve **survives container rebuilds** — unlike
in-memory structures that vanish on restart.

This closes the previously-missing "end-to-end learning curve" metric (P1 gap):
the system now exposes, across restarts, how merit / success-rate climb as the
court keeps evolving.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jarvis.learning_curve")

_MAX_POINTS = 2000  # hard cap to keep the file bounded

_lock = threading.Lock()


def _resolve_path() -> Path:
    """Resolve the store path, falling back safely if the data dir is unwritable."""
    base = os.environ.get("EMPEROR_DATA_DIR", "").strip()
    candidates: List[Path] = []
    if base:
        candidates.append(Path(base) / "learning_curve.json")
    candidates.append(Path.cwd() / "learning_curve.json")
    candidates.append(Path(tempfile.gettempdir()) / "emperor_learning_curve.json")
    for c in candidates:
        try:
            c.parent.mkdir(parents=True, exist_ok=True)
            return c
        except Exception:
            continue
    # Last resort: should never happen, but keep imports/usage alive.
    return candidates[-1]


_PATH = _resolve_path()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> Dict[str, Any]:
    try:
        if _PATH.exists():
            with open(_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("points", [])
            data.setdefault("next_round", len(data["points"]) + 1)
            return data
    except Exception as e:  # corrupt file -> start fresh
        logger.warning("[learning_curve] load failed (%s); resetting store", e)
    return {"points": [], "next_round": 1}


def _save(data: Dict[str, Any]) -> None:
    tmp = _PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(_PATH)


def record_evolve_round(court: Any) -> Optional[dict]:
    """Append one learning-curve point from the current ``Court`` state.

    Safe to call anywhere (scheduler tick, manual trigger, tests) — failures are
    swallowed so the evolution loop is never perturbed by the metric recorder.
    """
    try:
        snap = court.inspect.snapshot()
        ministers: Dict[str, dict] = {}
        for m in snap.ministers:
            ministers[m.name] = {
                "merit": round(float(getattr(m, "merit", 0.0) or 0.0), 3),
                "success_rate": round(float(getattr(m, "success_rate", 0.0) or 0.0), 3),
                "tasks": int(getattr(m, "tasks_completed", 0) or 0),
                "domain": getattr(m, "domain", "general"),
            }
        point = {
            "round": 0,  # filled in below under lock
            "ts": _now(),
            "avg_merit": round(float(getattr(court, "avg_merit", 0.0) or 0.0), 3),
            "success_rate": round(float(getattr(court, "success_rate", 0.0) or 0.0), 3),
            "active_ministers": int(getattr(snap, "active_count", 0) or 0),
            "ministers": ministers,
        }
        with _lock:
            data = _load()
            point["round"] = data["next_round"]
            data["points"].append(point)
            if len(data["points"]) > _MAX_POINTS:
                data["points"] = data["points"][-_MAX_POINTS:]
            data["next_round"] = data["next_round"] + 1
            _save(data)
        return point
    except Exception as e:  # noqa: BLE001
        logger.debug("[learning_curve] record skipped: %s", e)
        return None


def get_learning_curve() -> Dict[str, Any]:
    """Return the full time series for the dashboard."""
    with _lock:
        data = _load()
    return {
        "rounds": data["next_round"] - 1,
        "points": data["points"],
        "path": str(_PATH),
    }


def reset() -> None:
    """Clear the store (used by tests / manual reset)."""
    with _lock:
        _save({"points": [], "next_round": 1})
