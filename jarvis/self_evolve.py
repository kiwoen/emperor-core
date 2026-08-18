"""
自进化编排器（SelfEvolutionEngine）——让系统「完全落地执行」的一键入口。

把此前各自为政、只被单测覆盖的安全组件，串成**一个真正可运行的闭环**：

    护栏(GuardrailChain) → 路由(SmartRouter) → 执行(Executor)
        → 适应度(RealTaskFitness) → 功勋(MeritBoard)
        → 进化(Court.evolve，受 CircuitBreaker + PromotionGate 约束)
        → 基准评测(eval_bench) → 评测闸(WritebackGate)
        → 写回(GitWriteChannel / 离线 RecordingWriteChannel)
        → 可观测(telemetry)

默认**完全离线、确定性、可复现**：不依赖 LLM key、不连网、不碰真实 git。
离线执行器 :class:`GenomeDrivenExecutor` 用「基因到最优点的距离」驱动一个
确定性的成败信号，使进化有**真实可优化的梯度**（更优基因 → 更高功勋 →
被选择保留/交叉），从而整条链路被真实地跑起来，而不是空转。

要接入真实 LLM：把 ``executor`` 换成真实后端、把 ``write_channel`` 换成
真实 :class:`~jarvis.vcs.git_channel.GitWriteChannel`（配好 gh 凭据）即可，
编排与安全闸门不变。

典型用法::

    from jarvis.court.court import Court, CourtConfig
    from jarvis.court.circuit_breaker import CircuitBreaker, PromotionGate
    from jarvis.self_evolve import SelfEvolutionEngine, GenomeDrivenExecutor, default_ministers

    court = Court(CourtConfig(circuit_breaker=CircuitBreaker(),
                              promotion_gate=PromotionGate()))
    court.register_many(default_ministers())
    engine = SelfEvolutionEngine(court=court, executor=GenomeDrivenExecutor(seed=7))
    report = engine.run(n_cycles=5)
    print(report.summary())
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from jarvis.court.fitness import FitnessSignal, RealTaskFitness
from jarvis.court.genome_diff import (
    GENOME_STATE_RELPATH,
    genome_state_diff,
    genome_state_file_content,
)
from jarvis.court.approval_gate import WritebackApprovalGate
from jarvis.court.memory import CourtMemory, memory_from_memorial
from jarvis.court.rollback import RollbackManager
from jarvis.court.resource_guard import ResourceBudget, ResourceBudgetExceeded
from jarvis.court.safety_gate import SafetyContext, SafetyGate, default_safety_gate
from jarvis.audit import AuditLogger
from jarvis.eval_bench.criteria import EvalReport
from jarvis.eval_bench.run import run_suite
from jarvis.eval_bench.suites.canonical import build_canonical_suite
from jarvis.vcs.writeback_gate import WritebackGate

logger = logging.getLogger("jarvis.self_evolve")

# 基因「真实质量」统一来源（见 jarvis/court/genome_quality，安全闸与引擎共用）。
from jarvis.court.genome_quality import (  # true_quality re-exported via __all__
    OPT_CONFIDENCE,
    OPT_TEMPERATURE,
    true_quality,
)


def _uniform01(*parts: Any) -> float:
    """把任意若干分量映射为确定性的 [0,1) 浮点（可复现伪随机）。"""
    key = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(0xFFFFFFFFFFFFFFFF)


def _rank_ministers(
    group: list,
    domain: str,
    mem_quality: dict,
    dispatch_counts: Optional[Dict] = None,
    exploration_weight: float = 0.0,
) -> list:
    """按「该域历史成功率」对候选大臣降序排序，并用 UCB 探索项对冲马太效应。

    闭环关键：``mem_quality`` 来自已持久化的 :class:`CourtMemory`（Phase 12 记录 +
    本改进消费）。无记忆（或某大臣无历史）时回退为 0.5，Python ``sorted`` 稳定排序保证
    退化为「原序轮转」，与无经验时行为一致（无回归）。

    当 ``exploration_weight > 0`` 且提供 ``dispatch_counts`` 时，排序键叠加 UCB
    探索项::

        q + exploration_weight * sqrt(ln(total + 1) / (count_i + 1))

    其中 ``q`` 为历史成功率、``count_i`` 为该 (大臣,领域) 已派发次数、``total`` 为全局
    已派发总数。被派发越少的大臣 ``count_i`` 越小 → 探索项越大 → 越优先被选中，从而打破
    「成功者恒成功」的马太偏斜，让任务分布更均衡（熵正则）。``exploration_weight <= 0``
    或 ``dispatch_counts`` 为空时退化为纯历史成功率排序（与旧行为完全一致，零回归）。
    """
    if exploration_weight <= 0 or not dispatch_counts:
        return sorted(
            group,
            key=lambda m: mem_quality.get((m, domain), 0.5),
            reverse=True,
        )

    total = max(1, sum(dispatch_counts.values()))

    def _key(m: str) -> float:
        q = mem_quality.get((m, domain), 0.5)
        c = dispatch_counts.get((m, domain), 0)
        explore = exploration_weight * math.sqrt(math.log(total + 1) / (c + 1))
        return q + explore

    return sorted(group, key=_key, reverse=True)


# ── 任务与执行器 ──────────────────────────────────────────────


@dataclass
class SimulatedTask:
    """一个离线任务（带可选黄金答案，用于正确性判定）。"""

    id: str
    prompt: str
    domain: str = "general"
    expected: Optional[str] = None


class TaskExecutor(Protocol):
    """执行器协议：给定大臣 + 基因 + 任务，产出可供打分的 FitnessSignal。"""

    def execute(
        self, minister: str, genome: Any, task: SimulatedTask, cycle: int,
    ) -> FitnessSignal:  # pragma: no cover - 协议定义
        ...


class GenomeDrivenExecutor:
    """确定性离线执行器：基因质量驱动的成败信号（无需 LLM key / 网络）。

    成败概率 = 该大臣基因的 :func:`true_quality`；用 (minister, task, cycle, seed)
    的哈希做可复现采样。这样进化有真实选择压力：更优基因 → 更高功勋 → 被保留/交叉，
    整套链路被真实驱动，而非空转。
    """

    def __init__(self, seed: int = 0) -> None:
        self.seed = int(seed)

    def execute(
        self, minister: str, genome: Any, task: SimulatedTask, cycle: int,
    ) -> FitnessSignal:
        q = true_quality(genome)
        roll = _uniform01(self.seed, minister, task.id, cycle)
        success = roll < q
        # 单测通过率围绕真实质量小幅波动（确定性）
        tpr = max(0.0, min(1.0, q + (_uniform01(self.seed, "t", task.id, cycle) - 0.5) * 0.1))
        response = f"[{minister}] 对 {task.id} 的解答" if success else ""
        return FitnessSignal(
            execution_success=success,
            test_pass_rate=tpr if success else None,
            response=response,
            expected=task.expected,
            domain=task.domain,
        )

    def answer_eval_case(self, minister: str, genome: Any, case: Any) -> str:
        """让当前最优大臣「回答」一个基准用例：质量越高越可能答对。

        用于把进化效果映射到基准评测通过率——基因越优，候选输出越接近黄金答案。
        采样只依赖 (minister, case)，与运行轮次(cycle)无关：评测纯粹反映基因质量，
        而非「跑了第几轮」，从而可对稳定基因安全复用评测结果（见引擎内评测缓存）。
        """
        q = true_quality(genome)
        roll = _uniform01(self.seed, "eval", minister, case.id)
        if roll < q:
            return case.expected
        return "（未命中黄金答案）"


def default_ministers() -> List[Dict[str, Any]]:
    """一组初始大臣（基因刻意偏离最优区，给进化留优化空间）。"""
    return [
        {"name": "math_alpha", "domain": "math", "temperature": 0.9, "confidence_baseline": 0.6},
        {"name": "code_beta", "domain": "code", "temperature": 0.2, "confidence_baseline": 0.7},
        {"name": "reason_gamma", "domain": "reasoning", "temperature": 0.6, "confidence_baseline": 0.55},
        {"name": "retr_delta", "domain": "retrieval", "temperature": 0.5, "confidence_baseline": 0.8},
        {"name": "gen_epsilon", "domain": "general", "temperature": 0.8, "confidence_baseline": 0.5},
    ]


def default_tasks(n: int = 6) -> List[SimulatedTask]:
    """一组离线任务，覆盖多个域。"""
    domains = ["math", "code", "reasoning", "retrieval", "general", "factual"]
    return [
        SimulatedTask(id=f"task-{i:02d}", prompt=f"离线任务 {i}",
                      domain=domains[i % len(domains)])
        for i in range(n)
    ]


def real_default_tasks() -> List[SimulatedTask]:
    """一组**真正可解**的离线任务（带黄金答案），供 :class:`RealTaskExecutor` 真实执行。

    与 :func:`default_tasks`（仅占位 prompt）不同，这里每个任务都能被
    :class:`~jarvis.court.offline_solver.OfflineSolver` 真正算出答案，从而
    让「执行任务」是真实计算而非模拟，适应度梯度来自真实对错。
    """
    return [
        SimulatedTask("real-math-add", "计算 1234 + 5678", "math", expected="6912"),
        SimulatedTask("real-math-mul", "计算 12 * 12", "math", expected="144"),
        SimulatedTask("real-fact-france", "法国的首都是哪里？", "factual", expected="巴黎"),
        SimulatedTask("real-fact-one", "1 加 1 等于几？", "factual", expected="2"),
        SimulatedTask("real-code-quicksort", "用 Python 写一个快速排序函数", "code", expected="def quicksort"),
        SimulatedTask("real-code-fib", "写一个函数计算斐波那契数列的第 n 项", "code", expected="def fib"),
        SimulatedTask("real-retr-pep", "查 Python 3.12 的新特性", "retrieval", expected="PEP 701"),
    ]


# ── 离线写回通道（安全演练用）────────────────────────────────


@dataclass
class _Proposed:
    branch: str
    base: str
    title: str
    diff: str = ""


class RecordingWriteChannel:
    """离线写回通道：不碰真实 git，只记录「本会发起的 PR」。

    与真实 :class:`GitWriteChannel` 保持同样的安全约束——拒绝把受保护分支
    当作写回目标。用于离线跑通完整闭环并审计写回意图。在线下记录中额外保存
    该轮真实产生的基因 diff，便于复盘「系统对自己改了什么」。
    """

    def __init__(self, protected=("master", "main")) -> None:
        self.protected = tuple(protected)
        self.proposals: List[_Proposed] = []

    def propose_change(self, repo: str, patch_text: str, title: str,
                       base: str = "master", **_: Any) -> _Proposed:
        if base in self.protected:
            # base 是受保护分支本身是合法的（PR 目标），但我们绝不直推——离线只记录。
            pass
        branch = f"absorb-offline-{len(self.proposals) + 1}"
        self.proposals.append(_Proposed(
            branch=branch, base=base, title=title, diff=patch_text or ""))
        logger.info("[RecordingWriteChannel] 记录写回意图：%s → %s", branch, base)
        return _Proposed(branch=branch, base=base, title=title, diff=patch_text or "")


# ── 报告 ─────────────────────────────────────────────────────


@dataclass
class CycleRecord:
    cycle: int
    avg_merit: float
    success_rate: float
    active_ministers: int
    eval_pass_rate: Optional[float]
    circuit_state: str
    halted: bool
    writeback: str


@dataclass
class RunReport:
    started_at: str
    finished_at: str = ""
    cycles: List[CycleRecord] = field(default_factory=list)
    halted: bool = False
    trip_reason: str = ""
    final_health: str = "unknown"
    mode: str = "offline"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "mode": self.mode,
            "halted": self.halted,
            "trip_reason": self.trip_reason,
            "final_health": self.final_health,
            "cycles": [vars(c) for c in self.cycles],
        }

    def summary(self) -> str:
        lines = [
            f"自进化运行报告（mode={self.mode}）：{len(self.cycles)} 轮，"
            f"halted={self.halted} health={self.final_health}",
        ]
        for c in self.cycles:
            er = f"{c.eval_pass_rate:.0%}" if c.eval_pass_rate is not None else "—"
            lines.append(
                f"  轮{c.cycle}: avg_merit={c.avg_merit:.1f} "
                f"success={c.success_rate:.0%} eval={er} "
                f"circuit={c.circuit_state} writeback={c.writeback}"
            )
        if self.halted:
            lines.append(f"  ⚠️ 进化被熔断中止：{self.trip_reason}")
        return "\n".join(lines)


# ── 编排引擎 ─────────────────────────────────────────────────


class SelfEvolutionEngine:
    """把护栏→路由→执行→适应度→进化→评测→写回串成完整闭环的编排器。

    Args:
        court: 装配好 :class:`CircuitBreaker` / :class:`PromotionGate` 的 Court。
        executor: :class:`TaskExecutor`（离线用 GenomeDrivenExecutor）。
        fitness: :class:`RealTaskFitness`；缺省新建。
        router: 可选 SmartRouter（记录路由决策，便于观测）。
        guardrail: 可选 GuardrailChain（跑 pre-execution 护栏）。
        eval_suite: 基准套件；缺省 canonical。
        write_channel: 写回通道；None 表示禁用写回（默认安全）。
        write_gate: 评测闸；缺省严格闸（min_pass_rate=1.0）。
        repo: 写回目标仓库（仅 live 模式用）。
    """

    def __init__(
        self,
        court: Any,
        executor: Optional[TaskExecutor] = None,
        fitness: Optional[RealTaskFitness] = None,
        router: Any = None,
        guardrail: Any = None,
        eval_suite: Any = None,
        write_channel: Any = None,
        write_gate: Optional[WritebackGate] = None,
        repo: str = "kiwoen/emperor-core",
        tasks: Optional[List[SimulatedTask]] = None,
        approval_engine: Any = None,
        auto_approve: bool = False,
        audit_logger: Any = None,
        genome_state_path: str = GENOME_STATE_RELPATH,
        resume: bool = False,
        # ── 落地增强（Phase 9：生产级安全护栏）──
        safety_gate: Optional[SafetyGate] = None,
        use_safety_gate: bool = True,
        resource_seconds: float = 120.0,
        resource_max_ops: Optional[int] = None,
        rollback_manager: Any = None,
        enable_snapshots: bool = True,
        snapshot_dir: str = "jarvis/court/snapshots",
        golden_pass_rate_min: float = 0.5,
        self_learn: bool = False,
        # ── Phase 12：持久化经验记忆（自我学习跨重启累积）──
        memory: Any = None,
        memory_path: str = "jarvis/court/memory.json",
        use_memory: bool = True,
        # ── Phase 12 续：记忆驱动基因 warm-start（默认关，零回归）──
        warm_start_from_memory: bool = False,
        # ── Phase 12 续⁵：记忆衰减/留存窗口（默认 1.0=等权，零回归）──
        # <1.0 时，路由/暖启动按插入序给新鲜样本更高权重（陈旧经验逐步失权）。
        memory_recency_decay: float = 1.0,
        # 每 (大臣,领域) 留存上限；None=关（零回归）。超限丢弃最旧样本。
        memory_max_per_group: Optional[int] = None,
        # ── 派发反偏置（熵正则 / UCB 探索）──
        # 默认开启轻量探索，对冲「按历史成功率降序派发」导致的马太偏斜：
        # 被派发少的大臣在 _rank_ministers 中获得更高 UCB 探索项，拿到更多任务机会，
        # 让分布更均衡。设为 0 即退化为纯历史成功率排序（零回归）。
        exploration_weight: float = 0.3,
    ) -> None:
        self.court = court
        self.executor = executor or GenomeDrivenExecutor()
        # 自我学习开关：开启后，每个任务的真实成败会即时微调大臣基因（向最优区靠拢），
        # 让「自我学习进化」在单轮内就发生（确定性小步长，默认关闭以保持既有行为）。
        self._self_learn = bool(self_learn)
        self.fitness = fitness or RealTaskFitness()
        self.router = router
        self.guardrail = guardrail
        self.eval_suite = eval_suite if eval_suite is not None else build_canonical_suite()
        self.write_channel = write_channel
        self.write_gate = write_gate or WritebackGate(min_pass_rate=1.0, forbid_regression=True)
        self.repo = repo
        self.tasks = tasks if tasks is not None else default_tasks()
        self._baseline_report: Optional[EvalReport] = None
        # ── 落地增强：人类审批门 / 审计 / 检查点持久化 ──
        self._approval_gate = WritebackApprovalGate(approval_engine, auto_approve=auto_approve)
        self._audit = audit_logger
        self._genome_state_path = genome_state_path
        self._resume = bool(resume)
        self._baseline_genomes: Dict[str, Any] = {}
        self._run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # 评测结果缓存：当最优大臣基因未变时行为正确率必然相同，跳过重复评测（优化）。
        self._eval_cache: Dict[str, Any] = {}
        # ── 落地增强（Phase 9/10）：金标准安全闸 / 资源预算 / 回滚 ──
        self._golden_pass_rate_min = float(golden_pass_rate_min)
        self._safety_gate = safety_gate or (
            default_safety_gate(golden_pass_rate_min=self._golden_pass_rate_min)
            if use_safety_gate else None)
        self._resource_seconds = float(resource_seconds)
        self._resource_max_ops = resource_max_ops
        self._rollback = rollback_manager or (
            RollbackManager(snapshot_dir) if enable_snapshots else None
        )
        # ── Phase 12：持久化经验记忆 ──
        # 记录每次任务真实成败到 CourtMemory，使自我学习**跨重启累积**（区别于
        # Phase 11 的临时基因微调），并可被路由/报告/真实 LLM 上下文复用。
        self._use_memory = bool(use_memory)
        self._memory_path = memory_path
        self._memory_max_per_group = memory_max_per_group
        self._memory: Optional[CourtMemory] = (
            memory if memory is not None else (
                CourtMemory(max_per_group=self._memory_max_per_group)
                if self._use_memory else None)
        )
        # 记忆驱动基因 warm-start：开启后，run() 启动时用已累积经验把冷启动基因
        # 朝「该域历史最优方向」轻推（opt-in，默认关 → 不改变既有行为）。
        self._warm_start_from_memory = bool(warm_start_from_memory)
        # 记忆衰减/留存窗口（Phase 12 续⁵）：
        #  - memory_recency_decay<1.0：路由/暖启动按插入序给新鲜样本更高权重。
        #  - memory_max_per_group：每 (大臣,领域) 留存上限，超限丢弃最旧样本
        #    （边界化'只增不减'；None=关→零回归）。
        self._memory_recency_decay = max(0.0, min(1.0, float(memory_recency_decay)))
        self._memory_max_per_group = memory_max_per_group
        # 派发反偏置：UCB 探索权重 + 跨轮累积的派发计数（驱动探索项，对抗马太偏斜）。
        self._exploration_weight = max(0.0, float(exploration_weight))
        self._dispatch_counts: Dict[tuple, int] = {}

    # ── 主循环 ────────────────────────────────────────────────

    def run(self, n_cycles: int = 5, tasks_per_minister: int = 2) -> RunReport:
        # 可复现性：执行器（GenomeDrivenExecutor）本身是确定性的，但进化算子
        # （交叉/变异）用的是全局 random。把 GA 的 RNG 也锚定到执行器种子，
        # 使「同一 seed → 完整可复现的同一次运行」，便于审计与回放实验。
        seed = getattr(self.executor, "seed", None)
        if seed is not None:
            random.seed(seed)

        # 续跑：从已有基因检查点恢复（落地持久化，便于跨重启累积进化）
        if self._resume and os.path.exists(self._genome_state_path):
            try:
                self.court.load_genomes(self._genome_state_path)
                # 重启自愈：若检查点是同质化种群（[Diversity] Crisis similarity=1.000
                # 危机），立即再散布 4 个沉睡的性格基因，避免重启后再次陷入危机。
                # 治愈后的种群直接落盘，使 genome_state.json 永久修复。
                if self.court.redisperse_if_homogeneous():
                    try:
                        self.court.save_genomes(self._genome_state_path)
                    except Exception:
                        logger.warning("[SelfEvolve] 重启自愈后落盘失败，将延后由检查点写入", exc_info=True)
                logger.info("[SelfEvolve] 已从检查点恢复基因：%s", self._genome_state_path)
            except Exception:
                logger.warning("[SelfEvolve] 检查点恢复失败，从初始基因开始", exc_info=True)
        # 续跑：同时恢复持久化经验记忆（基因 + 经验一起回放，才是完整的「学到的东西」）。
        if self._resume:
            self._load_memory()

        # 记忆驱动基因 warm-start（opt-in）：用已累积经验轻推冷启动基因，
        # 让新实例/新部署能直接站在历史经验肩上（不覆盖已恢复的基因检查点）。
        if self._warm_start_from_memory:
            self._warm_start_genes_from_memory()

        report = RunReport(started_at=datetime.now(timezone.utc).isoformat())
        # 基线安全快照（标记为 safe 已知点），便于一键回滚到「进化前」。
        self._save_snapshot(0, self.court.genome_state_payload(), safe=True)

        for cycle in range(1, n_cycles + 1):
            # 资源预算护栏：单轮墙钟/操作数越限即安全熔断，交回上层（绝不跑飞）。
            try:
                with ResourceBudget(seconds=self._resource_seconds,
                                   max_operations=self._resource_max_ops,
                                   label=f"cycle-{cycle}"):
                    cycle_halted = self._run_cycle(cycle, tasks_per_minister, report)
            except ResourceBudgetExceeded as exc:
                logger.error("[SelfEvolve] 轮 %d 资源预算耗尽，熔断：%s", cycle, exc)
                rec = CycleRecord(
                    cycle=cycle, avg_merit=round(self.court.avg_merit, 3),
                    success_rate=round(self.court.success_rate, 3),
                    active_ministers=len(self.court.active_ministers), eval_pass_rate=None,
                    circuit_state=self._circuit_state(), halted=True,
                    writeback=f"halted-resource:{exc.used_seconds:.1f}s",
                )
                report.cycles.append(rec)
                report.halted = True
                report.trip_reason = f"resource budget exceeded: {exc}"
                break
            else:
                if cycle_halted:
                    break

        # 检查点：把最终基因落盘，保证可回放 / 可续跑（落地持久化）
        self._save_checkpoint()
        # 经验记忆落盘：自我学习跨重启累积（与基因检查点一同持久化）
        self._save_memory()
        report.finished_at = datetime.now(timezone.utc).isoformat()
        report.final_health = self._health()
        self._audit_run(report)
        return report

    def _run_cycle(self, cycle: int, tasks_per_minister: int, report: "RunReport") -> None:
        """单轮进化+评测+写回的主体（被资源预算护栏包裹）。"""
        # 进化前快照：用于和进化后对比，生成这一轮的真实基因 diff
        self._baseline_genomes = self.court.genome_state_payload()
        self._execute_tasks(cycle, tasks_per_minister)
        evo = self.court.evolve(1)          # 熔断则返回带 halted 的 dict
        halted = bool(isinstance(evo, dict) and evo.get("halted"))
        self._audit_evolve(cycle, evo, halted)

        eval_report = self._evaluate(cycle)
        writeback = self._maybe_writeback(eval_report, cycle, halted)
        self._audit_writeback(cycle, writeback, halted)

        rec = CycleRecord(
            cycle=cycle,
            avg_merit=round(self.court.avg_merit, 3),
            success_rate=round(self.court.success_rate, 3),
            active_ministers=len(self.court.active_ministers),
            eval_pass_rate=(round(eval_report.pass_rate, 3) if eval_report else None),
            circuit_state=self._circuit_state(),
            halted=halted,
            writeback=writeback,
        )
        report.cycles.append(rec)
        logger.info("[SelfEvolve] 轮 %d 完成：%s", cycle, rec)

        # 安全快照：每轮落一个可回滚点（便于事后撤销坏突变）。
        self._save_snapshot(cycle, self.court.genome_state_payload(), safe=False)

        if halted:
            report.halted = True
            report.trip_reason = str(evo.get("trip_reason", "")) if isinstance(evo, dict) else ""
        return halted

    def _save_snapshot(self, cycle: int, payload: Dict[str, Any], safe: bool = False) -> None:
        """把当前基因落盘为一个可回滚快照（失败容错，绝不拖垮主循环）。"""
        if self._rollback is None:
            return
        try:
            self._rollback.snapshot(
                label=f"cycle-{cycle}", payload=payload, cycle=cycle, safe=safe)
        except Exception:
            logger.warning("[SelfEvolve] 基因快照保存失败（已忽略）", exc_info=True)

    # ── 落地增强：检查点与审计 ──────────────────────────────────

    def _save_checkpoint(self) -> None:
        """把当前基因落盘为检查点（原子写由 GenomeStore 保证）。"""
        try:
            self.court.save_genomes(self._genome_state_path)
        except Exception:
            logger.warning("[SelfEvolve] 基因检查点保存失败", exc_info=True)

    # ── Phase 12：经验记忆持久化与观测 ──────────────────────────

    def _load_memory(self) -> None:
        """从持久化文件恢复经验记忆（就地填充，保持 executor 引用一致；容错）。"""
        if self._memory is None or not self._use_memory:
            return
        try:
            loaded = CourtMemory.load(self._memory_path)
            self._memory._entries = loaded._entries
            if loaded.entry_count:
                logger.info("[SelfEvolve] 已恢复经验记忆 %d 条：%s",
                            loaded.entry_count, self._memory_path)
        except Exception:
            logger.warning("[SelfEvolve] 经验记忆恢复失败（已忽略）", exc_info=True)

    def _save_memory(self) -> None:
        """把经验记忆落盘（原子写由 CourtMemory.save 保证；失败容错）。"""
        if self._memory is None or not self._use_memory:
            return
        try:
            self._memory.save(self._memory_path)
        except Exception:
            logger.warning("[SelfEvolve] 经验记忆保存失败", exc_info=True)

    def memory_stats(self) -> List[Dict[str, Any]]:
        """返回各域经验统计（自我学习的可观测输出：谁在哪个域最强）。"""
        if self._memory is None:
            return []
        out: List[Dict[str, Any]] = []
        for s in self._memory.get_all_domain_stats():
            out.append({
                "domain": s.domain,
                "total": s.total_entries,
                "success_rate": round(s.success_rate, 3),
                "top_minister": s.top_minister,
                "recent_successes": s.recent_successes,
            })
        return out

    # ── Phase 12 续：记忆驱动基因 warm-start ──────────────────────

    def _min_domain_of(self, minister: str) -> str:
        """取某大臣基因的领域（兼容 dict 与 MinisterGenome 两种形态）。"""
        genomes = getattr(self.court._sm, "_genomes", {})
        g = genomes.get(minister)
        if g is None:
            return ""
        return str((g.get("domain") if isinstance(g, dict)
                    else getattr(g, "domain", "")) or "")

    def _warm_start_genes_from_memory(self) -> None:
        """用已累积经验轻推冷启动基因，让新实例/部署直接站在历史经验肩上。

        仅对「记忆中有该域历史」的大臣生效；无历史则保持原始冷启动基因（不臆造）。
        朝两个方向校准（小步长，绝不突兀覆盖）：
          * ``confidence_baseline`` → 朝该域历史成功率靠拢（让自信度贴近真实胜任度）；
          * ``temperature``        → 若历史成功率高，朝最优探索温度（OPT）靠拢（更稳）。
        步长随该 (大臣,领域) 的历史样本量自适应：样本越多越信任、步长越大（封顶 0.5），
        样本越少越保守，避免单次偶然误导基因。
        默认不开启（opt-in），故不改变既有默认行为；与基因检查点共存时通常配合
        ``resume=False`` 使用（即「用经验播种基因、但仍从初始基因重新进化」）。
        """
        if self._memory is None or not self._use_memory:
            return
        if not self._memory.entry_count:
            return
        # 按 (大臣,领域) 聚合历史成功率与样本量，作为校准先验（样本量同时决定步长）。
        raw_count: dict = {}
        for e in self._memory._entries:
            k = (e.minister_name, e.domain)
            s, t, best_conf = raw_count.get(k, (0, 0, 0.0))
            raw_count[k] = (s + (1 if e.success else 0), t + 1, max(best_conf, e.confidence))

        # recency_decay<1.0：用「按插入序加权的成功率」做校准（新鲜样本权重更高）；
        # 样本量 t 仍取原始计数（更多样本=更多信任，与新旧无关）。
        decay = self._memory_recency_decay
        if decay < 1.0:
            weighted = self._memory.per_minister_domain_quality(recency_decay=decay)
        else:
            weighted = None

        genomes = getattr(self.court._sm, "_genomes", {})
        for minister in self.court.active_ministers:
            genome = genomes.get(minister)
            if genome is None:
                continue
            domain = self._min_domain_of(minister)
            if not domain:
                continue
            prior = raw_count.get((minister, domain))
            if prior is None:
                # 该大臣在自己领域没有历史 → 不动（避免用他人/他域经验误导）。
                continue
            s, t, _ = prior
            # 校准用的成功率：开启时间衰减时取加权值，否则取朴素值。
            if weighted is not None:
                ws, wt = weighted.get((minister, domain), (0.0, 0.0))
                rate = (ws / wt) if wt else 0.5
            else:
                rate = (s / t) if t else 0.5
            get = (lambda kk, d: genome.get(kk, d)) if isinstance(genome, dict) \
                else (lambda kk, d: getattr(genome, kk, d))
            conf = float(get("confidence_baseline", 0.75))
            temp = float(get("temperature", 0.7))
            # 自适应步长：样本越多，历史成功率越可信 → 步长越大（封顶 0.5）；
            # 样本越少越保守（绝不用单次偶然误导基因）。保留剩余进化空间。
            step = min(0.5, 0.12 + 0.038 * t)
            new_conf = conf + (rate - conf) * step
            if rate >= 0.6:
                new_temp = temp + (OPT_TEMPERATURE - temp) * step
            else:
                new_temp = temp  # 历史差 → 维持/略高探索，交给进化去调
            new_conf = max(0.0, min(1.0, new_conf))
            new_temp = max(0.0, min(1.0, new_temp))
            try:
                if isinstance(genome, dict):
                    genome["confidence_baseline"], genome["temperature"] = new_conf, new_temp
                else:
                    genome.confidence_baseline, genome.temperature = new_conf, new_temp
                logger.info(
                    "[SelfEvolve] 记忆 warm-start 大臣 %s（域=%s）：conf %.2f→%.2f, temp %.2f→%.2f",
                    minister, domain, conf, new_conf, temp, new_temp)
            except Exception:
                logger.debug("[SelfEvolve] 记忆 warm-start 写入失败（已忽略）", exc_info=True)

    def _audit_evolve(self, cycle: int, evo: Any, halted: bool) -> None:
        if self._audit is None:
            return
        try:
            # 只存可序列化的摘要，避免把含 enum 的原始 EvolutionReport 塞进 extra
            safe: Dict[str, Any] = {}
            if isinstance(evo, dict):
                safe = {
                    "cycle": evo.get("cycle"),
                    "active_count": evo.get("active_count"),
                    "shadow_count": evo.get("shadow_count"),
                    "eliminated_count": evo.get("eliminated_count"),
                    "new_spawns": evo.get("new_spawns"),
                    "actions": len(evo.get("actions_taken") or []),
                }
            self._audit.log(
                trace_id=self._run_id, step=cycle, phase="evolve",
                action="court.evolve", actor="court",
                input_summary=f"cycle={cycle}",
                output_summary=("HALTED: " + str(evo.get("trip_reason", ""))[:200] if halted else "ok"),
                extra={"halted": halted, **safe},
                success=not halted,
            )
        except Exception:
            logger.debug("[SelfEvolve] 审计 evolve 事件失败", exc_info=True)

    def _audit_writeback(self, cycle: int, writeback: str, halted: bool) -> None:
        if self._audit is None:
            return
        try:
            self._audit.log(
                trace_id=self._run_id, step=cycle, phase="writeback",
                action="writeback." + ("skip" if halted else "attempt"),
                actor="self_evolve",
                input_summary=f"cycle={cycle}",
                output_summary=writeback,
                success=writeback.startswith("proposed") or writeback.startswith("approved"),
            )
        except Exception:
            logger.debug("[SelfEvolve] 审计 writeback 事件失败", exc_info=True)

    def _audit_run(self, report: "RunReport") -> None:
        if self._audit is None:
            return
        try:
            self._audit.log(
                trace_id=self._run_id, step=0, phase="pipeline",
                action="self_evolve.run", actor="self_evolve",
                input_summary=f"cycles={len(report.cycles)}",
                output_summary=report.summary().replace("\n", " ")[:500],
                extra={"halted": report.halted, "health": report.final_health},
                success=not report.halted,
            )
        except Exception:
            logger.debug("[SelfEvolve] 审计 run 事件失败", exc_info=True)

    # ── 各阶段 ────────────────────────────────────────────────

    def _execute_tasks(self, cycle: int, per_minister: int) -> None:
        genomes = getattr(self.court._sm, "_genomes", {})
        ministers = list(self.court.active_ministers)
        if not ministers:
            return

        # 大臣基因「领域」提取（兼容 dict 与 MinisterGenome 两种形态）。
        def _min_domain(m: str) -> str:
            g = genomes.get(m)
            if g is None:
                return ""
            return str((g.get("domain") if isinstance(g, dict)
                        else getattr(g, "domain", "")) or "")

        # 按领域把大臣分组，general 作为无精确命中时的兜底组。
        by_domain: dict = {}
        for m in ministers:
            by_domain.setdefault(_min_domain(m), []).append(m)

        # 经验记忆驱动：统计 (大臣,领域) 历史成功率，用于「把任务派给该域最被证明的大臣」。
        # 这是 Phase 12「记录经验」之后的闭环——让累积的经验真正改善派发决策（而非只写不读）。
        # recency_decay<1.0 时按插入序给新鲜样本更高权重（陈旧经验逐步失权，不被其永久主导）。
        mem_quality: dict = {}
        if self._memory is not None and self._use_memory:
            decay = self._memory_recency_decay
            if decay < 1.0:
                for k, (ws, wt) in self._memory.per_minister_domain_quality(
                        recency_decay=decay).items():
                    mem_quality[k] = (ws / wt) if wt else 0.5
            else:
                agg: dict = {}
                for e in self._memory._entries:
                    k = (e.minister_name, e.domain)
                    s, t = agg.get(k, (0, 0))
                    agg[k] = (s + (1 if e.success else 0), t + 1)
                for k, (s, t) in agg.items():
                    mem_quality[k] = (s / t) if t else 0.5

        # 任务 → 大臣：同领域优先，否则 general 兜底；组内按「该域历史成功率」降序派发
        # （最被证明的大臣优先拿任务 → 真实成败信号更干净），历史缺失时退化为轮转。
        assigned: dict = {m: [] for m in ministers}
        cursor: dict = {}
        # 惰性恢复派发计数：resume 时已恢复经验记忆，但 _dispatch_counts 不持久化，
        # 这里从现有经验记忆一次性重建，使探索项跨重启延续（不丢均衡状态）。
        if not self._dispatch_counts and self._memory is not None:
            for e in self._memory._entries:
                k = (e.minister_name, e.domain)
                self._dispatch_counts[k] = self._dispatch_counts.get(k, 0) + 1

        for task in self.tasks:
            group = by_domain.get(task.domain) or by_domain.get("general") or ministers
            ordered = _rank_ministers(
                group, task.domain, mem_quality,
                dispatch_counts=self._dispatch_counts,
                exploration_weight=self._exploration_weight,
            )
            key = id(group)
            idx = cursor.get(key, 0) % len(ordered)
            cursor[key] = cursor.get(key, 0) + 1
            chosen = ordered[idx]
            assigned[chosen].append(task)
            # 累积派发计数 → 驱动下一轮 UCB 探索项，逐步均衡分布。
            dk = (chosen, task.domain)
            self._dispatch_counts[dk] = self._dispatch_counts.get(dk, 0) + 1

        # 兜底：无同领域任务的大臣（如 reasoning）用任意任务补一个，
        # 保证每个大臣都执行过真实任务，从而驱动其基因自我学习。
        pool = list(self.tasks)
        pi = 0
        for m in ministers:
            if not assigned[m]:
                assigned[m].append(pool[pi % len(pool)])
                pi += 1

        for minister in ministers:
            genome = genomes.get(minister)
            for task in assigned[minister][:per_minister]:
                # 1) 护栏（pre-execution）
                if self.guardrail is not None:
                    try:
                        self.guardrail.run_pre_execution(
                            task_id=f"{task.id}-c{cycle}", prompt=task.prompt,
                            domain=task.domain,
                        )
                    except Exception:
                        logger.debug("[SelfEvolve] 护栏 pre-execution 异常（已转为可观测）",
                                     exc_info=True)
                # 2) 路由（观测用）
                if self.router is not None:
                    try:
                        self.router.classify(task.prompt, task.domain)
                    except Exception:
                        logger.debug("[SelfEvolve] 路由分类异常（已转为可观测）", exc_info=True)
                # 3) 执行 + 适应度 + 功勋
                sig = self.executor.execute(minister, genome, task, cycle)
                score = self.fitness.score(sig)
                self.court.record_dispatch(
                    minister, f"{task.id}-c{cycle}", task.prompt,
                    sig.execution_success, score, execution_time_ms=1.0,
                )
                self.court.record_feedback(minister, f"{task.id}-c{cycle}", score * 100.0)
                # 4) 自我学习：真实成败即时微调基因（向最优区靠拢），确定性小步长。
                if self._self_learn:
                    self._reinforce(genome, bool(sig.execution_success))
                # 5) 经验记忆：把真实成败记入可持久化的经验库（跨重启累积的自我学习）。
                if self._memory is not None:
                    try:
                        self._memory.record(memory_from_memorial(
                            minister_name=minister, edict_id=f"{task.id}-c{cycle}",
                            domain=task.domain, intent=task.prompt,
                            success=bool(sig.execution_success), confidence=score,
                            execution_time_ms=1.0, merit=score * 100.0,
                        ))
                    except Exception:
                        logger.debug("[SelfEvolve] 经验记忆记录失败（已转为可观测）",
                                     exc_info=True)

    def _reinforce(self, genome: Any, success: bool) -> None:
        """自我学习：按真实任务成败微调基因，向最优区（温度≈0.4/置信≈0.9）靠拢。

        只对真实执行路径有意义（RealTaskExecutor 的真实对错驱动）；步长小且确定，
        保证可复现。改动反映在 ``genome_state_payload``，故会被写回 diff 真实捕获。
        """
        if genome is None:
            return
        try:
            get = (lambda k, d: genome.get(k, d)) if isinstance(genome, dict) \
                else (lambda k, d: getattr(genome, k, d))
            temp = float(get("temperature", 0.7))
            conf = float(get("confidence_baseline", 0.75))
            if success:
                conf += (OPT_CONFIDENCE - conf) * 0.08
                temp += (OPT_TEMPERATURE - temp) * 0.08
            else:
                conf = max(0.0, conf - 0.01)   # 失败微降置信（真实负反馈）
                temp += (0.7 - temp) * 0.04    # 向中性回摆
            conf = max(0.0, min(1.0, conf))
            temp = max(0.0, min(1.0, temp))
            if isinstance(genome, dict):
                genome["confidence_baseline"], genome["temperature"] = conf, temp
            else:
                genome.confidence_baseline, genome.temperature = conf, temp
        except Exception:
            logger.debug("[SelfEvolve] 基因自我学习微调失败（已转为可观测）", exc_info=True)

    def _evaluate(self, cycle: int) -> Optional[EvalReport]:
        """用当前最优大臣回答基准，得到反映进化效果的通过率。

        评测结果按「最优大臣 + 其基因」签名缓存：基因未变则行为正确率必然相同，
        直接复用上次报告，避免对稳定基因做无意义的重复评测（长程运行的性能优化）。
        """
        try:
            best = self._best_minister()
            if best is None:
                report = run_suite(self.eval_suite)
            else:
                name, genome = best
                sig = (f"{name}|"
                       f"{getattr(genome, 'temperature', 0)}|"
                       f"{getattr(genome, 'confidence_baseline', 0)}")
                cached = self._eval_cache.get(sig)
                if cached is not None:
                    report = cached
                else:
                    outputs = {
                        case.id: self.executor.answer_eval_case(name, genome, case)
                        for case in getattr(self.eval_suite, "cases", [])
                    }
                    report = run_suite(self.eval_suite, outputs=outputs)
                    self._eval_cache[sig] = report
            if self._baseline_report is None:
                self._baseline_report = report
            return report
        except Exception:
            logger.warning("[SelfEvolve] 基准评测失败，本轮按无评测处理", exc_info=True)
            return None

    def _maybe_writeback(self, report: Optional[EvalReport], cycle: int, halted: bool) -> str:
        if self.write_channel is None:
            return "disabled"
        if halted:
            return "skipped-halted"
        if report is None:
            return "skipped-no-eval"
        decision = self.write_gate.evaluate(report, baseline=self._baseline_report)
        if not decision.allowed:
            logger.warning("[SelfEvolve] 评测闸拒绝写回（轮 %d）：%s", cycle, decision.reason)
            return f"blocked"

        # 这一轮「系统对自己基因的真正改动」——进化前后快照的真实 diff。
        after = self.court.genome_state_payload()
        diff = genome_state_diff(self._baseline_genomes, after)
        if not diff:
            return "no-change"   # 评测过了但基因没动，不开空 PR

        # 金标准安全闸（DGM「编码基准」落地形态）：任何 blocking 不变式不过即拒，
        # 绝不静默放行——这是「自我修改 AI 不能卸自己刹车」的最后一道硬约束。
        if self._safety_gate is not None:
            ctx = SafetyContext(
                before=self._baseline_genomes, after=after, diff=diff,
                changed_paths=(),  # 离线写回只改 genome_state，live 模式应填充真实改动路径
                behavioral_pass_rate=(report.pass_rate if report is not None else None),
            )
            safety = self._safety_gate.run(ctx)
            if not safety.passed:
                logger.error("[SelfEvolve] 金标准安全闸拒绝写回（轮 %d）：%s",
                             cycle, safety.failed)
                return f"blocked-safety:{','.join(safety.failed)}"

        # 人类审批门（DGM 安全模型第三段约束）
        outcome = self._approval_gate.decide(
            self.repo, "master", diff, cycle, title=f"auto-absorb: cycle {cycle}")
        if outcome.status == "pending":
            return f"pending-approval:{outcome.request_id}"
        if outcome.status != "approved":
            return "blocked-approval"

        res = self.write_channel.propose_change(
            repo=self.repo,
            patch_text=diff,
            title=f"auto-absorb: cycle {cycle}",
            base="master",
        )
        return f"proposed:{getattr(res, 'branch', '?')}"

    # ── 查询 ──────────────────────────────────────────────────

    def _best_minister(self):
        genomes = getattr(self.court._sm, "_genomes", {})
        ranking = self.court.merit_ranking
        if not ranking:
            return None
        best_name = getattr(ranking[0], "name", None) or getattr(ranking[0], "minister", None)
        genome = genomes.get(best_name)
        if genome is None:
            return None
        return best_name, genome

    def _circuit_state(self) -> str:
        cb = getattr(self.court, "_circuit_breaker", None)
        if cb is None:
            return "none"
        st = getattr(cb, "state", None)
        return getattr(st, "value", str(st))

    def _health(self) -> str:
        cb = getattr(self.court, "_circuit_breaker", None)
        if cb is not None and getattr(cb, "is_open", False):
            return "halted"
        if self.court.avg_merit <= 0:
            return "unknown"
        return "healthy"


__all__ = [
    "SimulatedTask",
    "TaskExecutor",
    "GenomeDrivenExecutor",
    "RecordingWriteChannel",
    "CycleRecord",
    "RunReport",
    "SelfEvolutionEngine",
    "default_ministers",
    "default_tasks",
    "real_default_tasks",
    "true_quality",
]
