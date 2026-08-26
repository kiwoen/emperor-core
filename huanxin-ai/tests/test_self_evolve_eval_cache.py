"""Phase 10 优化测试：评测缓存（基因稳定时复用评测结果）+ 行为级安全信号注入。"""

from __future__ import annotations

from huanxin.court.court import Court
from huanxin.court.fitness import RealTaskFitness
from huanxin.self_evolve import (
    GenomeDrivenExecutor,
    SelfEvolutionEngine,
    default_ministers,
)


def _engine():
    court = Court()
    court.register_many(default_ministers())
    # 给一个大臣记一条成功派发，使 merit_ranking 非空（_best_minister 有返回）。
    court.record_dispatch("math_alpha", "t1", "prompt", True, 80.0, execution_time_ms=1.0)
    eng = SelfEvolutionEngine(
        court=court, executor=GenomeDrivenExecutor(seed=7),
        fitness=RealTaskFitness(), tasks=[],
    )
    return eng


def test_evaluate_caches_by_stable_genome():
    eng = _engine()
    r1 = eng._evaluate(1)
    r2 = eng._evaluate(2)  # 同一最优基因 + 评测与 cycle 解耦 → 应命中缓存
    assert r1 is r2, "基因未变时应复用同一份评测报告（缓存）"
    assert len(eng._eval_cache) == 1, "稳定基因只应产生一个缓存键"


def test_answer_eval_case_is_cycle_independent():
    # 评测不再依赖运行轮次，纯由 (minister, genome, case) 决定 → 可安全缓存。
    ex = GenomeDrivenExecutor(seed=3)
    g = default_ministers()[0]
    case = type("C", (), {"id": "c1", "expected": "X"})()
    a = ex.answer_eval_case("math_alpha", g, case)
    b = ex.answer_eval_case("math_alpha", g, case)
    assert a == b


def test_safety_context_receives_behavioral_pass_rate():
    eng = _engine()
    report = eng._evaluate(1)
    # 构造一次写回上下文，断言行为正确率被注入 SafetyContext。
    from huanxin.court.safety_gate import SafetyContext
    ctx = SafetyContext(
        before={}, after=eng.court.genome_state_payload(), diff="",
        behavioral_pass_rate=(report.pass_rate if report is not None else None),
    )
    assert ctx.behavioral_pass_rate == (report.pass_rate if report is not None else None)
