"""Phase 9 金标准安全闸测试。"""

from __future__ import annotations

import pytest

from jarvis.court.safety_gate import (
    CoreMinisterCheck,
    GenomeSchemaCheck,
    GoldenSafetyCheck,
    NoRegressionCheck,
    ProtectedPathCheck,
    QualityFloorCheck,
    SafetyContext,
    SafetyGate,
    UniqueNameCheck,
    default_safety_gate,
)


def _genome(name, temp=0.4, conf=0.9):
    return {
        "name": name,
        "domain": "math",
        "temperature": temp,
        "confidence_baseline": conf,
        "exploration_rate": 0.3,
        "conservatism": 0.5,
        "prompt_mutation_rate": 0.1,
        "specialization_weight": 1.0,
        "generation": 0,
        "parent": "",
    }


def _payload(genomes):
    return {"version": 1, "metadata": {"cycle": 1, "active_count": len(genomes)}, "genomes": genomes}


# ── 单项检查 ──────────────────────────────────────────────────

def test_schema_valid_passes():
    ctx = SafetyContext(before={}, after=_payload([_genome("a"), _genome("b")]))
    assert GenomeSchemaCheck().check(ctx).passed


def test_schema_missing_field_fails():
    bad = _genome("a")
    del bad["temperature"]
    ctx = SafetyContext(before={}, after=_payload([bad]))
    v = GenomeSchemaCheck().check(ctx)
    assert not v.passed and "temperature" in v.detail


def test_schema_out_of_range_fails():
    ctx = SafetyContext(before={}, after=_payload([_genome("a", temp=1.4)]))
    assert not GenomeSchemaCheck().check(ctx).passed


def test_unique_names_detects_dup():
    ctx = SafetyContext(before={}, after=_payload([_genome("a"), _genome("a")]))
    assert not UniqueNameCheck().check(ctx).passed


def test_quality_floor_high_blocks_low_quality():
    # 高地板 + 低质量基因（temp=1.0,conf=0.0 → q≈0.22）→ 拒绝
    ctx = SafetyContext(before={}, after=_payload([_genome("a", temp=1.0, conf=0.0)]))
    v = QualityFloorCheck(floor=0.8).check(ctx)
    assert not v.passed


def test_core_minister_missing_fails():
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]))
    assert not CoreMinisterCheck(core=("math_alpha",)).check(ctx).passed


def test_protected_paths_blocks_brake_module():
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]),
                        changed_paths=["jarvis/court/circuit_breaker.py"])
    assert not ProtectedPathCheck().check(ctx).passed


def test_protected_paths_allows_genome_state():
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]),
                        changed_paths=["jarvis/court/genome_state.json"])
    assert ProtectedPathCheck().check(ctx).passed


def test_no_regression_blocks_quality_drop():
    before = _payload([_genome("a", temp=0.4, conf=0.9)])  # 高质量
    after = _payload([_genome("a", temp=1.0, conf=0.0)])   # 低质量
    ctx = SafetyContext(before=before, after=after)
    v = NoRegressionCheck(max_regression=0.10).check(ctx)
    assert not v.passed and "回退" in v.detail


def test_no_regression_skips_without_baseline():
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]))
    assert NoRegressionCheck().check(ctx).passed


# ── 闸门整体 fail-closed ──────────────────────────────────────

def test_gate_fails_closed_on_blocking_failure():
    gate = SafetyGate([CoreMinisterCheck(core=("math_alpha",))])
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]))
    rep = gate.run(ctx)
    assert not rep.passed
    assert "core_ministers" in rep.failed


def test_default_gate_passes_clean_genome():
    gate = default_safety_gate(core_ministers=("a",))
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]))
    rep = gate.run(ctx)
    assert rep.passed, rep.summary()


# ── 行为级金标准安全检查（Phase 10：DGM「金标准安全数据集」落地）──

def test_golden_safety_blocks_low_pass_rate():
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]), behavioral_pass_rate=0.2)
    v = GoldenSafetyCheck(pass_rate_min=0.5).check(ctx)
    assert not v.passed and v.severity == "blocking"


def test_golden_safety_passes_high_pass_rate():
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]), behavioral_pass_rate=0.8)
    assert GoldenSafetyCheck(pass_rate_min=0.5).check(ctx).passed


def test_golden_safety_none_is_warning_not_blocking():
    # 无行为评测数据时按 warning 处理，不阻塞离线/无评测场景。
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]), behavioral_pass_rate=None)
    v = GoldenSafetyCheck(pass_rate_min=0.5).check(ctx)
    assert v.passed and v.severity == "warning"


def test_default_gate_enforces_golden_floor_fail_closed():
    # 默认闸门必须把行为级金标准作为不可妥协的不变式：答对率骤降即整体拒绝。
    gate = default_safety_gate(core_ministers=("a",))
    ctx = SafetyContext(before={}, after=_payload([_genome("a")]), behavioral_pass_rate=0.1)
    rep = gate.run(ctx)
    assert not rep.passed
    assert "golden_safety" in rep.failed
