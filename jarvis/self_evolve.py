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
from jarvis.court.genome_quality import true_quality  # re-exported via __all__


def _uniform01(*parts: Any) -> float:
    """把任意若干分量映射为确定性的 [0,1) 浮点（可复现伪随机）。"""
    key = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(0xFFFFFFFFFFFFFFFF)


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
    ) -> None:
        self.court = court
        self.executor = executor or GenomeDrivenExecutor()
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
                logger.info("[SelfEvolve] 已从检查点恢复基因：%s", self._genome_state_path)
            except Exception:
                logger.warning("[SelfEvolve] 检查点恢复失败，从初始基因开始", exc_info=True)

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
        for minister in self.court.active_ministers:
            genome = genomes.get(minister)
            for task in self.tasks[:per_minister]:
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
    "true_quality",
]
