"""P3.1 telemetry + emit_telemetry 测试。"""

from __future__ import annotations

import json
import os
import sys

import pytest

from huanxin.court.circuit_breaker import CircuitBreaker
from huanxin.eval_bench.criteria import EvalReport
from huanxin.telemetry import MinisterTelemetry, collect, write_js, write_json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import emit_telemetry  # noqa: E402


def _report(passed: int, total: int) -> EvalReport:
    pr = passed / total if total else 0.0
    return EvalReport(cases=total, passed=passed, failed=total - passed,
                      pass_rate=pr, per_domain={"math": pr})


def test_collect_healthy_when_perfect_eval():
    snap = collect(circuit_breaker=CircuitBreaker(), eval_report=_report(10, 10))
    assert snap.health == "healthy"
    assert snap.evaluation["pass_rate"] == 1.0
    assert snap.circuit["state"] == "closed"


def test_collect_halted_when_breaker_open():
    cb = CircuitBreaker()
    cb._state = cb._state.OPEN  # 直接置为熔断
    snap = collect(circuit_breaker=cb, eval_report=_report(10, 10))
    assert snap.health == "halted"
    assert snap.circuit["is_open"] is True


def test_collect_degraded_on_partial_eval():
    snap = collect(eval_report=_report(8, 10))
    assert snap.health == "degraded"


def test_collect_graceful_with_no_sources():
    snap = collect()
    assert snap.health == "unknown"
    d = snap.to_dict()
    assert d["ministers"] == [] and d["evaluation"] == {}


def test_ministers_and_events_aggregated():
    snap = collect(
        ministers=[MinisterTelemetry(name="a", merit=80.0),
                   MinisterTelemetry(name="b", merit=40.0)],
        evolution_events=[{"action": "promote"}, {"action": "eliminate"},
                          {"action": "promote"}],
        cost_total=12.5, cost_budget=10.0,
    )
    assert len(snap.ministers) == 2
    assert snap.evolution["event_count"] == 3
    assert snap.evolution["promotions"] == 2
    assert snap.evolution["eliminations"] == 1
    assert snap.cost["over_budget"] is True


def test_write_json_and_js(tmp_path):
    snap = collect(eval_report=_report(10, 10))
    jp = write_json(snap, str(tmp_path / "telemetry.json"))
    data = json.loads(open(jp, encoding="utf-8").read())
    assert data["schema_version"] == "1.0"
    sp = write_js(snap, str(tmp_path / "telemetry.js"))
    js = open(sp, encoding="utf-8").read()
    assert js.startswith("window.TELEMETRY = ")
    # JS 内嵌的 JSON 可被解析
    payload = js[len("window.TELEMETRY = "):].rstrip().rstrip(";")
    assert json.loads(payload)["health"] == "healthy"


def test_emit_telemetry_produces_files(tmp_path):
    out = str(tmp_path / "telemetry")
    rc = emit_telemetry.main(["--out", out, "--demo"])
    assert rc == 0
    js = open(os.path.join(out, "telemetry.js"), encoding="utf-8").read()
    assert js.startswith("window.TELEMETRY = ")
    data = json.loads(open(os.path.join(out, "telemetry.json"), encoding="utf-8").read())
    # canonical 基准黄金答案应全过
    assert data["evaluation"]["pass_rate"] == 1.0
    assert data["health"] == "healthy"
    # demo 模式应带大臣数据
    assert len(data["ministers"]) == 4
    # dashboard.html 被复制进输出目录
    assert os.path.exists(os.path.join(out, "dashboard.html"))
