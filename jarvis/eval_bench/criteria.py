"""
P0.6 评测基准的数据契约。

定义三个核心数据结构：

* :class:`EvalCase`   — 单个离线基准用例（含显式 ``gold_validator``）。
* :class:`EvalResult` — 单个用例的裁判结果（passed / score / reason）。
* :class:`EvalReport` — 一次运行的聚合报告（cases / pass_rate / per_domain）。

设计原则：裁判只回答"通过 / 不通过"，``score`` 只能是 1.0 或 0.0，
**绝不**用连续的"accuracy"数字伪装事实正确性（这是区别于
``jarvis.llm_judge`` 旧启发式的关键）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# (output, expected) -> is_correct
GoldValidator = Callable[[str, str], bool]


@dataclass
class EvalCase:
    """单个离线基准用例。

    Attributes:
        input: 喂给被测系统的任务 / 问题。
        expected: 黄金答案参考文本（供 validator / 归一化匹配使用）。
        gold_validator: 可调用对象 ``(output, expected) -> bool``，当且仅当
            产出的 *output* 事实正确时返回 ``True``。**绝不**依赖关键词重叠。
        domain: 逻辑分组（math / code / retrieval / factual / refusal / …）。
        tags: 自由标签，用于过滤 / 报告。
        id: 稳定标识符，用于报告与输出查找；缺省时由内容生成。
    """

    input: str
    expected: str
    gold_validator: GoldValidator
    domain: str = "general"
    tags: List[str] = field(default_factory=list)
    id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.id is None:
            slug = re.sub(r"\W+", "_", (self.input or "case")).strip("_")
            self.id = slug[:48] or "case"


@dataclass
class EvalResult:
    """单个 :class:`EvalCase` 的裁判结果。"""

    case_id: str
    domain: str
    passed: bool
    score: float  # 通过=1.0，不通过=0.0（无连续造假 accuracy）
    reason: str
    output: str = ""
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "domain": self.domain,
            "passed": self.passed,
            "score": round(self.score, 4),
            "reason": self.reason,
            "output": self.output,
            "diagnostics": self.diagnostics,
        }


@dataclass
class EvalReport:
    """一次 :func:`jarvis.eval_bench.run.run_suite` 运行的聚合结果。"""

    cases: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    per_domain: Dict[str, float] = field(default_factory=dict)
    results: List[EvalResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cases": self.cases,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 4),
            "per_domain": {k: round(v, 4) for k, v in self.per_domain.items()},
            "results": [r.to_dict() for r in self.results],
        }

    def summary(self) -> str:
        lines = [
            f"EvalReport: cases={self.cases} passed={self.passed} failed={self.failed}",
            f"  pass_rate={self.pass_rate:.1%}",
        ]
        for dom, rate in sorted(self.per_domain.items()):
            lines.append(f"  [{dom}] pass_rate={rate:.1%}")
        return "\n".join(lines)


__all__ = ["EvalCase", "EvalResult", "EvalReport", "GoldValidator"]
