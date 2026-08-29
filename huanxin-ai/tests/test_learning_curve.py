"""Focused branch tests for the persistent learning curve store."""

from __future__ import annotations

import json
from types import SimpleNamespace

import huanxin.learning_curve as learning_curve


def _court():
    ministers = [
        SimpleNamespace(name="alpha", merit=0.75, success_rate=0.8, tasks_completed=4, domain="math"),
        SimpleNamespace(name="beta", merit=None, success_rate=None, tasks_completed=None, domain=None),
    ]
    snapshot = SimpleNamespace(ministers=ministers, active_count=2)
    return SimpleNamespace(
        inspect=SimpleNamespace(snapshot=lambda: snapshot),
        avg_merit=0.5,
        success_rate=0.6,
    )


def test_record_and_reset(monkeypatch, tmp_path):
    path = tmp_path / "learning_curve.json"
    monkeypatch.setattr(learning_curve, "_PATH", path)

    point = learning_curve.record_evolve_round(_court())
    assert point is not None
    assert point["round"] == 1
    assert point["ministers"]["alpha"]["merit"] == 0.75
    assert point["ministers"]["beta"]["merit"] == 0.0

    curve = learning_curve.get_learning_curve()
    assert curve["rounds"] == 1
    assert len(curve["points"]) == 1

    learning_curve.reset()
    assert learning_curve.get_learning_curve()["rounds"] == 0
    assert learning_curve.get_learning_curve()["points"] == []


def test_corrupt_store_is_replaced(monkeypatch, tmp_path):
    path = tmp_path / "learning_curve.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(learning_curve, "_PATH", path)

    point = learning_curve.record_evolve_round(_court())
    assert point is not None
    assert point["round"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["next_round"] == 2


def test_record_failure_is_swallowed(monkeypatch, tmp_path):
    path = tmp_path / "learning_curve.json"
    monkeypatch.setattr(learning_curve, "_PATH", path)

    broken = SimpleNamespace(inspect=SimpleNamespace(snapshot=lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert learning_curve.record_evolve_round(broken) is None


def test_max_points_keeps_latest_records(monkeypatch, tmp_path):
    path = tmp_path / "learning_curve.json"
    monkeypatch.setattr(learning_curve, "_PATH", path)
    payload = {
        "points": [{"round": i} for i in range(learning_curve._MAX_POINTS)],
        "next_round": learning_curve._MAX_POINTS + 1,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    point = learning_curve.record_evolve_round(_court())
    assert point["round"] == learning_curve._MAX_POINTS + 1
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored["points"]) == learning_curve._MAX_POINTS
    assert stored["points"][0]["round"] == 2
