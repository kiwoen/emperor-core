"""
self_evolve_config — 自进化运行的 YAML 配置加载（落地部署用）。

把「跑多少轮 / 用什么随机种子 / 闸阈值 / 是否接入人类审批 / 检查点路径」从
硬编码里抽出来，便于在不同环境（离线演示 / CI / 生产）用一份配置驱动，
而不用改代码。纯标准库 + PyYAML（PyYAML 缺失时退化为内置默认，保证离线可用）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "cycles": 5,
    "tasks_per_minister": 2,
    "seed": 0,
    "repo": "kiwoen/huanxin-ai",
    "genome_state_path": "huanxin/court/genome_state.json",
    "resume": False,
    "auto_approve": False,            # 生产必须为 False（需人工审批）
    "use_approval_engine": False,     # 是否接入 ApprovalEngine 人类审批门
    "use_audit": False,               # 是否写入不可篡改审计库
    "writeback": "record",            # record(离线记录) | none(禁用) | live(真实 PR)
    "write_gate": {
        # 离线演示用宽松闸（仅为展示真实 diff 写回）；生产应改为
        # {"min_pass_rate": 1.0, "forbid_regression": True}
        "min_pass_rate": 0.0,
        "forbid_regression": False,
    },
    "circuit_breaker": {
        "drop_fraction": 0.25,
        "consecutive_negative": 4,
        "min_cycles_before_trip": 2,
    },
    "promotion_gate": {
        "required_consecutive_gains": 2,
        "min_merit": 50.0,
    },
    "enable_auto_elimination": False,  # 安全默认：淘汰仍冻结（dry-run）
    # ── Phase 9：生产级安全护栏 ──
    "use_safety_gate": True,           # 金标准安全闸（fail-closed，写回前最后一道）
    "quality_floor": 0.05,             # 任一基因质量不得低于此地板
    "core_ministers": ["math_alpha", "reason_gamma"],  # 核心大臣一个都不能少
    "max_regression": 0.10,            # 金标准大臣平均质量允许的最大回退
    "resource_seconds": 120.0,         # 单轮墙钟预算（越限即熔断，防跑飞）
    "resource_max_ops": None,          # 单轮操作数上限（如 LLM 调用次数），None=不限制
    "enable_snapshots": True,          # 每轮写回前落安全快照（可回滚）
    "snapshot_dir": "huanxin/court/snapshots",
    "golden_pass_rate_min": 0.5,       # 行为级金标准地板：最优大臣基准答对率不得低于此（防 reward-hacking）
    # ── Phase 11：真实任务执行 + 自我学习 ──
    "executor": "auto",                # sim(基因模拟) | real(真实离线求解/真实 LLM) | auto(默认 real)
    "self_learn": True,                # 真实成败即时微调基因（向最优区靠拢），真实自我学习
    "task_file": "",                   # 自定义真实任务文件（YAML/JSON：[{id,prompt,domain,expected}]）
    "tasks": [],                       # 内联任务（优先级最高）
    # ── Phase 12：持久化经验记忆（自我学习跨重启累积）──
    "use_memory": True,                # 记录每次任务真实成败到经验库
    "memory_path": "huanxin/court/memory.json",  # 经验记忆持久化路径
    "warm_start_from_memory": False,   # 启动时用经验轻推冷启动基因（opt-in，默认关→零回归）
    # ── Phase 12 续⁵：记忆衰减/留存窗口 ──
    "memory_recency_decay": 1.0,       # 路由/暖启动的历史成功率权重；1.0=等权(零回归)，<1.0=新鲜样本权重更高
    "memory_max_per_group": None,      # 每(大臣,领域)留存上限，None=不封顶(零回归)
    # ── 派发反偏置（熵正则 / UCB 探索）──
    "exploration_weight": 0.3,         # >0 时按 UCB 探索项对冲马太偏斜（被派发少的大臣更优先）；0=纯历史成功率排序
}


@dataclass
class SelfEvolveConfig:
    """自进化运行配置（扁平化便于消费）。"""

    cycles: int = 5
    tasks_per_minister: int = 2
    seed: int = 0
    repo: str = "kiwoen/huanxin-ai"
    genome_state_path: str = "huanxin/court/genome_state.json"
    resume: bool = False
    auto_approve: bool = False
    use_approval_engine: bool = False
    use_audit: bool = False
    writeback: str = "record"
    write_gate: Dict[str, Any] = field(default_factory=dict)
    circuit_breaker: Dict[str, Any] = field(default_factory=dict)
    promotion_gate: Dict[str, Any] = field(default_factory=dict)
    enable_auto_elimination: bool = False
    # Phase 9：生产级安全护栏
    use_safety_gate: bool = True
    quality_floor: float = 0.05
    core_ministers: List[str] = field(default_factory=list)
    max_regression: float = 0.10
    resource_seconds: float = 120.0
    resource_max_ops: Optional[int] = None
    enable_snapshots: bool = True
    snapshot_dir: str = "huanxin/court/snapshots"
    golden_pass_rate_min: float = 0.5
    # Phase 11：真实任务执行 + 自我学习
    executor: str = "auto"
    self_learn: bool = True
    task_file: str = ""
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    # Phase 12：持久化经验记忆
    use_memory: bool = True
    memory_path: str = "huanxin/court/memory.json"
    warm_start_from_memory: bool = False
    memory_recency_decay: float = 1.0
    memory_max_per_group: Optional[int] = None
    exploration_weight: float = 0.3

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SelfEvolveConfig":
        d = {**DEFAULT_CONFIG, **(d or {})}
        return cls(
            cycles=int(d.get("cycles", 5)),
            tasks_per_minister=int(d.get("tasks_per_minister", 2)),
            seed=int(d.get("seed", 0)),
            repo=str(d.get("repo", "kiwoen/huanxin-ai")),
            genome_state_path=str(d.get("genome_state_path", "huanxin/court/genome_state.json")),
            resume=bool(d.get("resume", False)),
            auto_approve=bool(d.get("auto_approve", False)),
            use_approval_engine=bool(d.get("use_approval_engine", False)),
            use_audit=bool(d.get("use_audit", False)),
            writeback=str(d.get("writeback", "record")),
            write_gate=dict(d.get("write_gate", {})),
            circuit_breaker=dict(d.get("circuit_breaker", {})),
            promotion_gate=dict(d.get("promotion_gate", {})),
            enable_auto_elimination=bool(d.get("enable_auto_elimination", False)),
            use_safety_gate=bool(d.get("use_safety_gate", True)),
            quality_floor=float(d.get("quality_floor", 0.05)),
            core_ministers=list(d.get("core_ministers", []) or []),
            max_regression=float(d.get("max_regression", 0.10)),
            resource_seconds=float(d.get("resource_seconds", 120.0)),
            resource_max_ops=(int(d["resource_max_ops"]) if d.get("resource_max_ops") is not None else None),
            enable_snapshots=bool(d.get("enable_snapshots", True)),
            snapshot_dir=str(d.get("snapshot_dir", "huanxin/court/snapshots")),
            golden_pass_rate_min=float(d.get("golden_pass_rate_min", 0.5)),
            executor=str(d.get("executor", "auto")),
            self_learn=bool(d.get("self_learn", True)),
            task_file=str(d.get("task_file", "") or ""),
            tasks=list(d.get("tasks", []) or []),
            use_memory=bool(d.get("use_memory", True)),
            memory_path=str(d.get("memory_path", "huanxin/court/memory.json")),
            warm_start_from_memory=bool(d.get("warm_start_from_memory", False)),
            memory_recency_decay=float(d.get("memory_recency_decay", 1.0)),
            memory_max_per_group=(
                int(d["memory_max_per_group"])
                if d.get("memory_max_per_group") is not None else None),
            exploration_weight=float(d.get("exploration_weight", 0.3)),
        )


def load_config(path: str) -> SelfEvolveConfig:
    """从 YAML 加载配置；文件不存在或 PyYAML 缺失则返回默认配置。"""
    try:
        import yaml  # type: ignore
    except Exception:
        return SelfEvolveConfig.from_dict({})
    import os
    if not os.path.exists(path):
        return SelfEvolveConfig.from_dict({})
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return SelfEvolveConfig.from_dict(data)


__all__ = ["DEFAULT_CONFIG", "SelfEvolveConfig", "load_config"]
