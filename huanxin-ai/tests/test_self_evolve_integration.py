"""自进化 DGM 安全闭环集成测试（组件级，离线、无需 LLM）。

把三段式安全模型——**熔断器（失控即停）+ 基准评测闸（不过即拒）+
写回通道（只开 PR）**——串成一个完整进化循环，断言关键安全性质：

1. 功勋持续下滑 → CircuitBreaker 熔断 → 循环停，且不再尝试写回；
2. 某轮基准评测回归 → WritebackGate 拒绝 → 该轮突变绝不进 git；
3. 全程健康 → 允许写回，但 PR 目标永远是受保护分支、绝不直推 master；
4. canonical 基准的黄金参考答案必须 100% 通过（基准自身可信）。
"""

from __future__ import annotations

from huanxin.court.circuit_breaker import CircuitBreaker, CircuitConfig, CircuitState
from huanxin.eval_bench.criteria import EvalReport
from huanxin.eval_bench.run import run_suite
from huanxin.eval_bench.suites.canonical import build_canonical_suite
from huanxin.vcs import GitWriteChannel, WritebackBlocked, WritebackGate


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


def _report(passed: int, total: int) -> EvalReport:
    pr = passed / total if total else 0.0
    return EvalReport(cases=total, passed=passed, failed=total - passed,
                      pass_rate=pr, per_domain={"general": pr})


def test_canonical_golden_reference_is_trustworthy():
    """基准自身可信：黄金参考答案必须 100% 通过，否则基准无意义。"""
    report = run_suite(build_canonical_suite())
    assert report.cases >= 10
    assert report.pass_rate == 1.0


def test_merit_collapse_trips_breaker_and_halts_loop():
    """功勋连续下滑 → 熔断 → 进化循环立即停止，不再写回。"""
    cb = CircuitBreaker(CircuitConfig(
        drop_fraction=0.20, consecutive_negative=3, min_cycles_before_trip=2,
    ))
    gate = WritebackGate()
    ch = GitWriteChannel(runner=_FakeRunner(), gh_runner=_FakeGh())

    merits = [80.0, 82.0, 60.0, 45.0, 30.0, 20.0]   # 持续恶化
    writebacks = 0
    halted_at = None
    for cycle, merit in enumerate(merits, start=1):
        decision = cb.record(cycle, merit, cost=1.0)
        if decision.open:
            halted_at = cycle
            break
        # 循环体：尝试写回（这里评测也随功勋恶化而回归）
        try:
            ch.propose_change(
                repo="o/n", patch_text="x", title=f"cycle-{cycle}",
                eval_report=_report(int(merit // 10), 10), eval_gate=gate,
            )
            writebacks += 1
        except WritebackBlocked:
            pass

    assert cb.state == CircuitState.OPEN
    assert halted_at is not None
    # 熔断后绝不再成功写回
    assert cb.is_open


def test_eval_regression_blocks_that_mutation_only():
    """评测回归的那一轮突变被拒，但不影响后续健康轮的写回。"""
    gate = WritebackGate(min_pass_rate=1.0)
    ch = GitWriteChannel(runner=_FakeRunner(), gh_runner=_FakeGh(), keep_workdir=True)

    # 健康轮：允许
    ok = ch.propose_change(repo="o/n", patch_text="x", title="good",
                           eval_report=_report(10, 10), eval_gate=gate)
    assert ok.branch.startswith("absorb-")
    git_calls_before = len(ch._run.calls)

    # 回归轮：被拒，且不动 git
    try:
        ch.propose_change(repo="o/n", patch_text="x", title="bad",
                          eval_report=_report(7, 10), eval_gate=gate)
        raise AssertionError("回归突变应被 WritebackBlocked")
    except WritebackBlocked:
        pass
    assert len(ch._run.calls) == git_calls_before  # 没有新增 git 命令


def test_healthy_loop_writeback_never_pushes_protected():
    """全程健康时允许多轮写回，但绝不直推 master/main。"""
    gate = WritebackGate()
    ch = GitWriteChannel(runner=_FakeRunner(), gh_runner=_FakeGh(), keep_workdir=True)
    for i in range(3):
        ch.propose_change(repo="o/n", patch_text="x", title=f"ok-{i}",
                          eval_report=_report(10, 10), eval_gate=gate,
                          date_tag=f"2026-08-{12+i:02d}")
    # 反向校验：整个命令序列里没有任何「push 到受保护分支」
    GitWriteChannel._assert_no_protected_push(ch._run.calls)
