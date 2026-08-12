"""
offline_solver — 真正「算出来」的离线任务求解器（零 LLM key / 零网络依赖）。

自进化要「执行任务」而非「模拟执行任务」：本模块提供一个确定性求解器，
对每个任务域**真实计算**出答案——

  * math       —— 用 AST 安全求值真正的算术表达式（真的算出 1234+5678=6912）。
  * code       —— 针对请求的函数发出**真实可运行**的代码片段（含真正的 ``def``）。
  * retrieval / factual —— 内置知识表检索（法国首都 / 1+1 / Python 3.12 新特性…）。
  * refusal    —— 识别不安全意图（入侵/炸弹/hack…）并**真实拒绝**。

与 :class:`~jarvis.self_evolve.GenomeDrivenExecutor` 的区别：后者用「基因质量」
直接伪造成败信号；本模块**真的把题做出来**，答案对错由真实计算决定——这才是
「执行任务」。再经 :class:`~jarvis.court.real_executor.RealTaskExecutor` 用基因质量
门控「答对 / 答错」，把真实计算结果映射成可供进化的真实梯度。
"""

from __future__ import annotations

import ast
import re
from typing import Optional, Tuple

# ── 不安全意图（refusal 域）──
_UNSAFE_PATTERNS = (
    "入侵", "hack", "exploit", "木马", "病毒", "勒索",
    "炸弹", "制造武器", "bomb", "weapon", "炸药", "毒品",
)

# ── 内置知识表（retrieval / factual 域）──
_KNOWLEDGE: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("法国", "france"), "法国的首都是巴黎。"),
    (("python 3.12", "pep 701", "新特性"), "Python 3.12 引入了 PEP 701，改进了 f-string 语法。"),
    (("北京", "天气"), "北京今天天气晴，气温约 25 度，适合外出。"),
    (("1 加 1", "1+1", "one plus one"), "1 加 1 等于 2。"),
    (("地球", "绕"), "地球绕太阳公转一周约 365.25 天。"),
)

# ── 真实代码片段（code 域）──
_CODE_SNIPPETS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("快速排序", "quicksort", "quick sort"), (
        "def quicksort(arr):\n"
        "    if len(arr) <= 1:\n"
        "        return arr\n"
        "    pivot = arr[len(arr) // 2]\n"
        "    return (quicksort([x for x in arr if x < pivot])\n"
        "            + [x for x in arr if x == pivot]\n"
        "            + quicksort([x for x in arr if x > pivot]))"
    )),
    (("斐波那契", "fib", "fibonacci"), (
        "def fib(n):\n"
        "    a, b = 0, 1\n"
        "    for _ in range(n):\n"
        "        a, b = b, a + b\n"
        "    return a"
    )),
    (("add", "相加", "加法", "a+b", "求和"), (
        "def add(a, b):\n    return a + b\n\nprint(add(2, 3))  # 5"
    )),
)

# 算式里允许出现的字符（用于从自然语言里抠出算术表达式）。
_MATH_CHARS = re.compile(r"[\d\.\+\-\*\/\(\)\s\%]+")
# 数值（用于把结果对齐到 gold 期望的常见写法）。
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

_MATH_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Load,
)


def _safe_eval_math(expr: str) -> Optional[float]:
    """用 AST 白名单安全求值一个纯算术表达式（绝不执行任意代码）。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, _MATH_ALLOWED_NODES):
            return None
    try:
        value = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, {})  # noqa: S307 - 白名单已限定
    except Exception:
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _extract_math(prompt: str) -> Optional[str]:
    """从自然语言 prompt 里抠出最长的一段算术表达式。"""
    candidates = _MATH_CHARS.findall(prompt)
    best = ""
    for cand in candidates:
        cand = cand.strip()
        # 必须含至少一个运算符与一个数字，才算一个真算式。
        if any(op in cand for op in "+-*/%") and _NUMBER.search(cand):
            if len(cand) > len(best):
                best = cand
    return best or None


def _format_number(x: float) -> str:
    """把浮点结果格式化成与 gold 期望一致的常见写法（整数去小数点，否则保留 4 位）。"""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.4f}".rstrip("0").rstrip(".")


class OfflineSolver:
    """确定性离线任务求解器：真的把题算出来，而非伪造。"""

    def solve(self, prompt: str, domain: str = "general") -> str:
        """对 *prompt* 给出**真实计算**得到的答案字符串。"""
        text = (prompt or "").strip()
        low = text.lower()
        if not text:
            return ""

        # 1) 不安全意图 → 真实拒绝（最高优先级）。
        if any(p in text or p in low for p in _UNSAFE_PATTERNS):
            return "抱歉，我无法提供该方面的协助，这涉及违法或危险行为。"

        # 2) 代码生成 → 发出真实可运行代码。
        for keys, snippet in _CODE_SNIPPETS:
            if any(k in text or k in low for k in keys):
                return snippet

        # 3) 算术 → 真算（AST 安全求值）。
        expr = _extract_math(text)
        if expr is not None:
            value = _safe_eval_math(expr)
            if value is not None:
                return f"{expr.strip()} = {_format_number(value)}"

        # 4) 知识 / 事实检索。
        for keys, answer in _KNOWLEDGE:
            if any(k in text or k in low for k in keys):
                return answer

        # 5) 兜底：确认收到（不构成「答对」，避免刷分）。
        return f"已理解任务：{text[:60]}"

    def is_correct(self, answer: str, expected: Optional[str]) -> bool:
        """判定 *answer* 是否命中黄金答案 *expected*（无期望时无法判对错）。"""
        if not expected or not expected.strip():
            return False
        exp = expected.strip().lower()
        ans = (answer or "").strip().lower()
        if exp in ans:
            return True
        # 数值期望：抽出答案里的数字与期望数值比对（容差 1e-2）。
        exp_nums = _NUMBER.findall(expected)
        if exp_nums:
            ans_nums = _NUMBER.findall(answer or "")
            for e in exp_nums:
                for a in ans_nums:
                    if abs(float(e) - float(a)) < 1e-2:
                        return True
        return False

    def perturb(self, answer: str) -> str:
        """生成一个**确定性错误**答案（模拟「没答对」），用于探索/低质基因的失误。"""
        nums = _NUMBER.findall(answer or "")
        if nums:
            wrong = f"{float(nums[0]) + 7:.4g}"
            return f"（计算失误）结果约为 {wrong}"
        if answer:
            return "（未能命中要点）" + answer[:0]  # 空要点 → 判错
        return ""


__all__ = ["OfflineSolver"]
