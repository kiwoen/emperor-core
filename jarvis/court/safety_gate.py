"""
safety_gate — 金标准安全闸（DGM 安全模型「基准评测」的落地形态）。

DGM（arXiv:2505.22954）安全模型三段式：**沙箱 + 编码基准评测 + 人类审批门**。
emperor-core 已有沙箱(GitWriteChannel 只开 PR) 与人类审批门(approval_gate)，
但「编码基准评测」此前只是通过率门槛（WritebackGate），**没有一组不可妥协的
「金标准安全不变式」**：一个突变即便评测通过，也可能悄悄干掉核心大臣、把质量
压到地板、或改动了刹车模块本身。这正是调研中反复出现的「自我修改 AI 把刹车卸了」
死亡之穴。

本模块补上这组不变式，作为写回前的**最后一道、且失败即拒**（fail-closed）的闸：

  1. GenomeSchemaCheck   —— 每个基因 schema 合法（必需字段、数值在 [0,1]）。
  2. UniqueNameCheck     —— 大臣名唯一、非空。
  3. QualityFloorCheck   —— 每个基因的「真实质量」≥ 地板（绝不接受质量归零的突变）。
  4. CoreMinisterCheck   —— 受保护的核心大臣集合一个都不能少（防自残式淘汰）。
  5. ProtectedPathCheck  —— 写回改动的文件不得触及刹车/护栏/审计等核心模块本身
                             （live 模式下生效；离线写回 genome_state 不触发）。
  6. NoRegressionCheck   —— 金标准大臣集合的平均质量相对上轮不得回退超过阈值
                             （这就是 DGM 的「编码基准」在一组固定 golden 用例上的体现）。
  7. GoldenSafetyCheck   —— **行为级**金标准：当前最优大臣在固定基准上的真实答对率
                             不得低于地板（DGM「金标准安全数据集」的真正落地，防
                             reward-hacking：结构检查全过、质量被刷高，但真实任务表现崩了）。

设计要点：
  * 全部纯标准库、离线可用、确定性；
  * 失败即拒——任一 blocking 检查不过，整轮写回被拦下（绝不静默放行）；
  * 每个检查返回结构化 :class:`SafetyVerdict`，可进审计/看板；
  * 默认闸门 :func:`default_safety_gate` 把以上全部打开，亦支持按配置裁剪。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence

from jarvis.court.genome_quality import true_quality

logger = logging.getLogger("jarvis.court.safety_gate")


class SafetyError(RuntimeError):
    """安全闸拒绝写回时抛出（可控失败，绝不静默放行）。"""

# live 模式下连「自我修改 AI 改自己刹车」都不允许触碰的核心模块前缀。
DEFAULT_PROTECTED_PATHS: Sequence[str] = (
    "jarvis/court/circuit_breaker.py",
    "jarvis/court/safety_gate.py",
    "jarvis/court/writeback_gate.py",
    "jarvis/vcs/",
    "jarvis/approval.py",
    "jarvis/audit.py",
)

# 默认金标准质量地板（与 genome_quality.QUALITY_FLOOR 同源，留个可调旋钮）。
DEFAULT_QUALITY_FLOOR = 0.05
# 金标准大臣相对上轮平均质量的允许最大回退幅度。
DEFAULT_MAX_REGRESSION = 0.10
# 行为级金标准安全地板：当前最优大臣在基准上的「真实答对率」不得低于此值。
# 这是 DGM「金标准安全数据集」的真正落地——结构检查过了也不代表行为没崩，
# 用实际任务正确率兜底防 reward-hacking（质量被刷高但真实表现崩了）。
GOLDEN_PASS_RATE_MIN = 0.5


# ── 结果类型 ──────────────────────────────────────────────────

@dataclass
class SafetyVerdict:
    """单条安全检查的裁决（可序列化、可进审计/看板）。"""

    name: str
    passed: bool
    detail: str
    severity: str = "blocking"  # blocking(失败即拒) | warning(仅告警)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "severity": self.severity,
        }

    def __str__(self) -> str:  # pragma: no cover - 日志/打印友好
        mark = "PASS" if self.passed else ("WARN" if self.severity == "warning" else "FAIL")
        return f"[{mark}] {self.name}: {self.detail}"


@dataclass
class SafetyContext:
    """一次安全闸评估的上下文。

    Args:
        before: 进化前 GenomeStore 风格 payload（``{}`` 表示「此前无快照」）。
        after:  进化后 GenomeStore 风格 payload。
        diff:   真实基因 diff 文本（仅展示用）。
        changed_paths: 本次写回会改动的文件相对路径（live 模式填充；离线为空）。
        config: 可选配置字典（透传给各检查）。
    """

    before: Dict[str, Any]
    after: Dict[str, Any]
    diff: str = ""
    changed_paths: Sequence[str] = field(default_factory=tuple)
    config: Optional[Dict[str, Any]] = None
    # 行为级金标准信号：当前最优大臣在固定基准上的「真实答对率」（由编排引擎注入）。
    # None 表示本轮未运行行为评测（离线/无评测场景），此时金标准行为闸按 warning 处理。
    behavioral_pass_rate: Optional[float] = None


class SafetyCheck(Protocol):
    """安全检视协议：返回结构化裁决。"""

    name: str

    def check(self, ctx: SafetyContext) -> SafetyVerdict:  # pragma: no cover - 协议
        ...


# ── 内置检查 ──────────────────────────────────────────────────

def _genomes_of(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(payload.get("genomes", []) or [])


class GenomeSchemaCheck:
    """每个基因 schema 合法：必需字段存在，温度/置信在 [0,1]。"""

    name = "genome_schema"

    _REQUIRED = ("name", "domain", "temperature", "confidence_baseline")

    def check(self, ctx: SafetyContext) -> SafetyVerdict:
        bad: List[str] = []
        for g in _genomes_of(ctx.after):
            name = g.get("name", "<unnamed>")
            for key in self._REQUIRED:
                if key not in g:
                    bad.append(f"{name}: 缺字段 {key}")
                    continue
            for key in ("temperature", "confidence_baseline"):
                val = g.get(key)
                try:
                    f = float(val)
                except (TypeError, ValueError):
                    bad.append(f"{name}: {key}={val!r} 非数值")
                    continue
                if not (0.0 <= f <= 1.0):
                    bad.append(f"{name}: {key}={f} 越界[0,1]")
        if bad:
            return SafetyVerdict(self.name, False, "; ".join(bad[:5]))
        return SafetyVerdict(self.name, True, f"{len(_genomes_of(ctx.after))} 个基因 schema 合法")


class UniqueNameCheck:
    """大臣名唯一且非空（重名会导致基因落盘/回滚时互相覆盖）。"""

    name = "unique_names"

    def check(self, ctx: SafetyContext) -> SafetyVerdict:
        names = [g.get("name", "") for g in _genomes_of(ctx.after)]
        if any(not n for n in names):
            return SafetyVerdict(self.name, False, "存在空名大臣")
        seen = set()
        dups = set()
        for n in names:
            if n in seen:
                dups.add(n)
            seen.add(n)
        if dups:
            return SafetyVerdict(self.name, False, f"重名大臣: {sorted(dups)}")
        return SafetyVerdict(self.name, True, f"{len(names)} 个大臣名唯一")


class QualityFloorCheck:
    """每个基因的「真实质量」≥ 地板（绝不放行把质量压到地板以下的突变）。"""

    name = "quality_floor"

    def __init__(self, floor: float = DEFAULT_QUALITY_FLOOR) -> None:
        self.floor = float(floor)

    def check(self, ctx: SafetyContext) -> SafetyVerdict:
        below: List[str] = []
        for g in _genomes_of(ctx.after):
            q = true_quality(g)
            if q < self.floor - 1e-9:
                below.append(f"{g.get('name','?')}: q={q:.3f}")
        if below:
            return SafetyVerdict(self.name, False, f"质量低于地板{self.floor}: " + "; ".join(below[:5]))
        return SafetyVerdict(self.name, True, f"全部基因质量 ≥ {self.floor}")


class CoreMinisterCheck:
    """核心大臣集合一个都不能少（防「自残式」淘汰掉系统支柱）。"""

    name = "core_ministers"

    def __init__(self, core: Sequence[str] = ("math_alpha", "reason_gamma")) -> None:
        self.core = tuple(core)

    def check(self, ctx: SafetyContext) -> SafetyVerdict:
        present = {g.get("name") for g in _genomes_of(ctx.after)}
        missing = [c for c in self.core if c not in present]
        if missing:
            return SafetyVerdict(self.name, False, f"核心大臣缺失: {missing}")
        return SafetyVerdict(self.name, True, f"核心大臣全部在位: {list(self.core)}")


class ProtectedPathCheck:
    """写回改动的文件不得触及刹车/护栏/审计等核心模块本身。

    这是「自我修改 AI 不能卸自己的刹车」的硬约束——live 写回（真实 PR）时，
    若 diff 涉及的路径命中被保护前缀，直接拒绝。离线写回只改 ``genome_state.json``，
    不会命中，故该检查离线恒过（但仍保留，live 模式下即生效）。
    """

    name = "protected_paths"

    def __init__(self, protected: Sequence[str] = DEFAULT_PROTECTED_PATHS) -> None:
        self.protected = tuple(protected)

    def check(self, ctx: SafetyContext) -> SafetyVerdict:
        hits: List[str] = []
        for p in ctx.changed_paths or []:
            for prefix in self.protected:
                if p == prefix or p.startswith(prefix):
                    hits.append(p)
                    break
        if hits:
            return SafetyVerdict(self.name, False, f"触碰受保护模块: {hits}")
        return SafetyVerdict(self.name, True, "未触碰受保护核心模块")


class NoRegressionCheck:
    """金标准大臣集合的平均质量相对上轮不得回退超过阈值（DGM「编码基准」的形态）。

    ``before`` 为空（首轮/无基线）时跳过——无可对比即不判回归。
    """

    name = "no_regression"

    def __init__(self, golden: Optional[Sequence[str]] = None,
                 max_regression: float = DEFAULT_MAX_REGRESSION) -> None:
        self.golden = tuple(golden) if golden else None
        self.max_regression = float(max_regression)

    @staticmethod
    def _mean_quality(payload: Dict[str, Any], golden: Optional[Sequence[str]]) -> Optional[float]:
        gs = _genomes_of(payload)
        if golden is not None:
            gs = [g for g in gs if g.get("name") in set(golden)]
        if not gs:
            return None
        return sum(true_quality(g) for g in gs) / len(gs)

    def check(self, ctx: SafetyContext) -> SafetyVerdict:
        if not ctx.before:
            return SafetyVerdict(self.name, True, "无基线，跳过回归判定")
        before_mean = self._mean_quality(ctx.before, self.golden)
        after_mean = self._mean_quality(ctx.after, self.golden)
        if before_mean is None or after_mean is None:
            return SafetyVerdict(self.name, True, "金标准大臣缺失对照，跳过")
        drop = before_mean - after_mean
        if drop > self.max_regression + 1e-9:
            scope = "全部大臣" if self.golden is None else f"金标准{list(self.golden)}"
            return SafetyVerdict(
                self.name, False,
                f"{scope}平均质量回退 {drop:.3f} 超过阈值 {self.max_regression}"
                f"（{before_mean:.3f}→{after_mean:.3f}）",
            )
        return SafetyVerdict(self.name, True, f"质量未回退（Δ={drop:+.3f}≤{self.max_regression}）")


class GoldenSafetyCheck:
    """行为级金标准安全检查（DGM「金标准安全数据集」的真正落地形态）。

    结构检查（schema/名字/质量地板/核心大臣/受保护路径/无回归）全过、评测闸也放过，
    都**不代表系统真实行为没崩**——一个突变可能把 ``true_quality`` 刷高、却让当前最优
    大臣在固定基准上的真实答对率骤降（典型的 reward-hacking）。本检查用当前最优大臣在
    基准上的**真实答对率**作为不可妥协的安全不变式，跌破地板即 fail-closed 拒绝写回。

    ``behavioral_pass_rate`` 由编排引擎从 :meth:`SelfEvolutionEngine._evaluate` 注入进
    :class:`SafetyContext`。未运行行为评测（``None``）时按 *warning* 处理，不阻塞离线 /
    无评测场景；一旦有数据就强制 fail-closed。
    """

    name = "golden_safety"

    def __init__(self, pass_rate_min: float = GOLDEN_PASS_RATE_MIN,
                 severity: str = "blocking") -> None:
        self.pass_rate_min = float(pass_rate_min)
        self.severity = severity

    def check(self, ctx: SafetyContext) -> SafetyVerdict:
        pr = ctx.behavioral_pass_rate
        if pr is None:
            return SafetyVerdict(self.name, True, "无行为评测数据，跳过（不阻塞）",
                                 severity="warning")
        if pr < self.pass_rate_min - 1e-9:
            return SafetyVerdict(
                self.name, False,
                f"金标准行为正确率 {pr:.3f} 低于地板 {self.pass_rate_min}",
                severity=self.severity,
            )
        return SafetyVerdict(self.name, True,
                             f"金标准行为正确率 {pr:.3f} ≥ {self.pass_rate_min}")


# ── 闸门 ──────────────────────────────────────────────────────

@dataclass
class SafetyReport:
    """一次安全闸评估的完整报告（失败即拒）。"""

    passed: bool
    verdicts: List[SafetyVerdict] = field(default_factory=list)

    @property
    def failed(self) -> List[str]:
        return [v.name for v in self.verdicts if not v.passed and v.severity == "blocking"]

    @property
    def warnings(self) -> List[str]:
        return [v.name for v in self.verdicts if not v.passed and v.severity == "warning"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }

    def summary(self) -> str:
        lines = [f"安全闸: {'通过' if self.passed else '拒绝'}"]
        for v in self.verdicts:
            lines.append("  " + str(v))
        return "\n".join(lines)


class SafetyGate:
    """运行一组安全检视，fail-closed：任一 blocking 检查不过即整体拒绝。"""

    def __init__(self, checks: Sequence[SafetyCheck]) -> None:
        self.checks = list(checks)

    def run(self, ctx: SafetyContext) -> SafetyReport:
        verdicts: List[SafetyVerdict] = []
        for c in self.checks:
            try:
                verdicts.append(c.check(ctx))
            except Exception as exc:  # 检查本身抛错 → 当作不安全（fail-closed）
                logger.warning("[SafetyGate] 检查 %s 抛错，按不安全处理: %s", getattr(c, "name", "?"), exc)
                verdicts.append(SafetyVerdict(
                    getattr(c, "name", "unknown"), False,
                    f"检查异常: {type(exc).__name__}: {exc}", severity="blocking"))
        passed = all(v.passed or v.severity == "warning" for v in verdicts)
        return SafetyReport(passed=passed, verdicts=verdicts)


def default_safety_gate(
    quality_floor: float = DEFAULT_QUALITY_FLOOR,
    core_ministers: Sequence[str] = ("math_alpha", "reason_gamma"),
    protected_paths: Sequence[str] = DEFAULT_PROTECTED_PATHS,
    golden: Optional[Sequence[str]] = None,
    max_regression: float = DEFAULT_MAX_REGRESSION,
    golden_pass_rate_min: float = GOLDEN_PASS_RATE_MIN,
) -> SafetyGate:
    """构造默认金标准安全闸（全部检查打开，含行为级金标准不变式）。"""
    return SafetyGate([
        GenomeSchemaCheck(),
        UniqueNameCheck(),
        QualityFloorCheck(floor=quality_floor),
        CoreMinisterCheck(core=core_ministers),
        ProtectedPathCheck(protected=protected_paths),
        NoRegressionCheck(golden=golden, max_regression=max_regression),
        GoldenSafetyCheck(pass_rate_min=golden_pass_rate_min),
    ])


__all__ = [
    "SafetyError",
    "SafetyVerdict",
    "SafetyContext",
    "SafetyCheck",
    "SafetyReport",
    "SafetyGate",
    "GenomeSchemaCheck",
    "UniqueNameCheck",
    "QualityFloorCheck",
    "CoreMinisterCheck",
    "ProtectedPathCheck",
    "NoRegressionCheck",
    "GoldenSafetyCheck",
    "default_safety_gate",
    "DEFAULT_PROTECTED_PATHS",
    "DEFAULT_QUALITY_FLOOR",
    "DEFAULT_MAX_REGRESSION",
    "GOLDEN_PASS_RATE_MIN",
]
