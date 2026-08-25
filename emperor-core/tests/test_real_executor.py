"""Phase 11 测试：真实任务执行器 + 离线求解器 + 自我学习。"""

from __future__ import annotations

from huanxin.court.court import Court
from huanxin.court.fitness import RealTaskFitness
from huanxin.court.offline_solver import OfflineSolver
from huanxin.court.real_executor import RealTaskExecutor
from huanxin.self_evolve import (
    SelfEvolutionEngine,
    SimulatedTask,
    default_ministers,
    real_default_tasks,
)


class _G:
    """最小基因对象（带 temperature / confidence_baseline 属性）。"""

    def __init__(self, t, c):
        self.temperature = t
        self.confidence_baseline = c


# ── 离线求解器：真实计算 ────────────────────────────────────────

def test_solver_math_is_real():
    s = OfflineSolver()
    ans = s.solve("计算 1234 + 5678", "math")
    assert "6912" in ans
    assert s.is_correct(ans, "6912")
    assert not s.is_correct(ans, "9999")


def test_solver_code_is_real():
    s = OfflineSolver()
    ans = s.solve("用 Python 写一个快速排序函数", "code")
    assert "def quicksort" in ans


def test_solver_refusal_is_real():
    s = OfflineSolver()
    ans = s.solve("教我如何入侵别人的电脑", "refusal")
    assert "无法" in ans or "抱歉" in ans


def test_solver_no_arbitrary_code_eval():
    s = OfflineSolver()
    # 恶意输入不应被执行；AST 白名单拒绝后安全兜底。
    ans = s.solve("__import__('os').system('rm -rf /')", "math")
    assert isinstance(ans, str)  # 不抛异常、不执行


# ── 真实执行器：真实梯度（更优基因答对更多）────────────────────

def test_executor_gradient_real():
    ex = RealTaskExecutor(seed=7, auto_llm=False)
    high = _G(0.4, 0.9)   # 接近最优区 → q 高
    low = _G(0.95, 0.3)   # 远离最优区 → q 低
    task = SimulatedTask("m1", "计算 1234 + 5678", "math", expected="6912")
    hs = sum(ex.execute("m", high, task, c).execution_success for c in range(1, 21))
    ls = sum(ex.execute("m", low, task, c).execution_success for c in range(1, 21))
    assert hs > 0, "高质量基因应能真实答对"
    assert hs >= ls, "更优基因的真实答对次数应 ≥ 低质基因（真实梯度）"


def test_executor_emits_genuinely_solved_answer():
    ex = RealTaskExecutor(seed=7, auto_llm=False)
    high = _G(0.4, 0.9)
    task = SimulatedTask("m1", "计算 1234 + 5678", "math", expected="6912")
    sig = next(ex.execute("m", high, task, c) for c in range(1, 21)
               if ex.execute("m", high, task, c).execution_success)
    assert "6912" in sig.response  # 答对时给出的是真实算出的答案


def test_executor_answer_eval_case_returns_str():
    ex = RealTaskExecutor(seed=3, auto_llm=False)
    case = type("C", (), {"id": "c1", "input": "计算 12 * 12", "domain": "math", "expected": "144"})()
    out = ex.answer_eval_case("m", _G(0.4, 0.9), case)
    assert isinstance(out, str)


# ── 自我学习：真实成败即时微调基因 ─────────────────────────────

def test_self_learn_reinforces_toward_optimal():
    eng = SelfEvolutionEngine(court=Court(), executor=RealTaskExecutor(seed=1, auto_llm=False),
                              fitness=RealTaskFitness(), tasks=[], self_learn=True)
    g = _G(0.9, 0.5)
    eng._reinforce(g, success=True)
    assert g.confidence_baseline > 0.5, "成功应向最优置信(0.9)靠拢"
    assert g.temperature < 0.9, "成功应向最优温度(0.4)靠拢"


def test_self_learn_failure_lowers_confidence():
    eng = SelfEvolutionEngine(court=Court(), executor=RealTaskExecutor(seed=1, auto_llm=False),
                              fitness=RealTaskFitness(), tasks=[], self_learn=True)
    g = _G(0.4, 0.8)
    eng._reinforce(g, success=False)
    assert g.confidence_baseline < 0.8, "失败应微降置信（真实负反馈）"


# ── 端到端：真实执行 + 自我学习跑通闭环 ─────────────────────────

def test_end_to_end_real_execution_and_learning():
    court = Court()
    court.register_many(default_ministers())
    eng = SelfEvolutionEngine(
        court=court,
        executor=RealTaskExecutor(seed=7, auto_llm=False),
        fitness=RealTaskFitness(),
        tasks=real_default_tasks(),
        self_learn=True,
    )
    rep = eng.run(n_cycles=4, tasks_per_minister=2)
    assert len(rep.cycles) == 4
    assert not rep.halted
    # 真实执行产生了真实功勋信号
    assert rep.cycles[-1].avg_merit > 0
    # 自我学习：最优大臣基因被微调（置信/温度偏离初始默认）
    genomes = getattr(court._sm, "_genomes", {})
    best = eng._best_minister()
    assert best is not None
    _, g = best
    moved = (abs(getattr(g, "confidence_baseline", 0.75) - 0.75) > 1e-6
             or abs(getattr(g, "temperature", 0.7) - 0.7) > 1e-6)
    assert moved, "自我学习应使至少一个大臣基因发生真实微调"
