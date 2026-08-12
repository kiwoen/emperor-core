"""P2.3 WritebackGate + GitWriteChannel 评测闸集成测试。

核心断言（DGM 闭环：基准不过就绝不写回）：
- 评测达标（pass_rate=1.0）→ 允许写回，开 PR；
- 评测不达标 / 相对基线回归 / 某域跌破下限 / 空套件 → 抛 WritebackBlocked；
- 被拒时**绝不执行任何 git 子命令**（fail fast，无副作用）；
- 不传 eval_report 时保持向后兼容（直接开 PR）。
"""

from __future__ import annotations

import pytest

from jarvis.eval_bench.criteria import EvalReport, EvalResult
from jarvis.vcs import (
    GitWriteChannel,
    WritebackBlocked,
    WritebackGate,
)


def _result(case_id: str, domain: str, passed: bool) -> EvalResult:
    return EvalResult(
        case_id=case_id, domain=domain, passed=passed,
        score=1.0 if passed else 0.0, reason="ok" if passed else "fail",
        output="", diagnostics={},
    )


def _report(passed: int, total: int, per_domain=None) -> EvalReport:
    results = []
    for i in range(total):
        results.append(_result(f"c{i}", "general", passed=(i < passed)))
    pr = passed / total if total else 0.0
    return EvalReport(
        cases=total, passed=passed, failed=total - passed,
        pass_rate=pr, per_domain=per_domain or {"general": pr}, results=results,
    )


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


class _FakeGh:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()


def _channel():
    runner, gh = _FakeRunner(), _FakeGh()
    return GitWriteChannel(runner=runner, gh_runner=gh, keep_workdir=True), runner, gh


# ── 闸单元测试 ──────────────────────────────────────────────

def test_gate_allows_perfect_score():
    gate = WritebackGate(min_pass_rate=1.0)
    d = gate.evaluate(_report(10, 10))
    assert d.allowed


def test_gate_blocks_below_threshold():
    gate = WritebackGate(min_pass_rate=1.0)
    d = gate.evaluate(_report(9, 10))
    assert not d.allowed
    assert "通过率" in d.reason


def test_gate_blocks_regression_vs_baseline():
    gate = WritebackGate(min_pass_rate=0.8, forbid_regression=True)
    baseline = _report(10, 10)          # 基线 100%
    candidate = _report(9, 10)          # 候选 90%（≥0.8 但回归）
    d = gate.evaluate(candidate, baseline=baseline)
    assert not d.allowed
    assert "回归" in d.reason


def test_gate_blocks_domain_floor():
    gate = WritebackGate(min_pass_rate=0.5, min_domain_pass_rate=0.9)
    report = _report(5, 10, per_domain={"math": 1.0, "code": 0.5})
    d = gate.evaluate(report)
    assert not d.allowed
    assert "code" in d.reason


def test_gate_blocks_empty_suite():
    gate = WritebackGate()
    d = gate.evaluate(EvalReport(cases=0, passed=0, failed=0, pass_rate=0.0))
    assert not d.allowed
    assert "空" in d.reason


def test_gate_assert_allowed_raises():
    gate = WritebackGate(min_pass_rate=1.0)
    with pytest.raises(WritebackBlocked):
        gate.assert_allowed(_report(8, 10))


# ── 与 GitWriteChannel 集成 ────────────────────────────────

def test_writeback_blocked_before_any_git_command():
    ch, runner, gh = _channel()
    with pytest.raises(WritebackBlocked):
        ch.propose_change(
            repo="o/n", patch_text="x", title="t",
            eval_report=_report(9, 10),   # 不达标
        )
    # 关键：fail fast —— 任何 git/gh 子命令都不该执行
    assert runner.calls == []
    assert gh.calls == []


def test_writeback_allowed_opens_pr():
    ch, runner, gh = _channel()
    result = ch.propose_change(
        repo="o/n", patch_text="x", title="t",
        eval_report=_report(10, 10), date_tag="2026-08-12",
    )
    assert result.branch.startswith("absorb-")
    assert gh.calls, "评测通过应照常开 PR"


def test_backward_compatible_without_eval_report():
    ch, runner, gh = _channel()
    result = ch.propose_change(
        repo="o/n", patch_text="x", title="t", date_tag="2026-08-12",
    )
    assert result.branch.startswith("absorb-")
    assert gh.calls
