"""
Codex Reviewer — 多维加权代码审查引擎。

将「剧本八维审查」方法论迁移到软件工程：对一段代码进行系统性质量审查，
覆盖工程逻辑与工业执行两个层面：

- **工程逻辑层**：8 个独立审查维度，逐条定位问题与可优化项
- **类型适配层**：根据语言与代码类型（web 服务 / 数据管道 / CLI / 测试 / 库）
  应用不同权重与专项检查标准
- **严重度层**：每条问题标注 🔴严重 / 🟡中等 / 🟢轻微，并按严重度排序
- **诚实 N/A**：无法评估的维度如实标注「不适用」，绝不强行打分

> 设计原则（铁律）：
>   1. 类型适配优先 —— 不同语言/代码类型审查重点不同，禁止一刀切
>   2. 问题定位精确 —— 每条问题必须附 file:line 定位
>   3. 建议可执行 —— 修改建议须具体到动作，禁止仅写「加强」「改善」
>   4. 区分硬伤与偏好 —— 明确区分工业标准硬伤与风格偏好
>   5. 八维独立评分 —— 某一维度高分不弥补另一维度低分
>   6. 诚实 N/A —— 无法评估的维度如实说明，不强行映射

纯 Python、确定性、离线可跑（不依赖 LLM）。可选 LLM 增强钩子用于
语义级审查（如命名语义、设计合理性），但规则级审查始终由本引擎完成。
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("jarvis.codex.reviewer")


# ── 维度与严重度 ──────────────────────────────────────────────────


class Dimension(str, Enum):
    """八维审查维度。"""

    CORRECTNESS = "correctness"          # 正确性：能否正确运行、逻辑是否成立
    SECURITY = "security"                # 安全性：注入/ secrets / 危险调用
    PERFORMANCE = "performance"          # 性能：时间/空间复杂度反模式
    MAINTAINABILITY = "maintainability"  # 可维护性：复杂度/长度/命名
    TESTING = "testing"                  # 测试：覆盖/断言质量
    CONCURRENCY = "concurrency"          # 并发安全：锁/异步阻塞/共享可变状态
    OBSERVABILITY = "observability"      # 可观测性：日志/错误处理可见性
    DOCUMENTATION = "documentation"      # 文档：docstring/类型注解/模块说明


class Severity(str, Enum):
    """问题严重度。"""

    CRITICAL = "critical"  # 🔴 必须修改：会导致运行失败、安全漏洞、数据损坏
    MAJOR = "major"        # 🟡 强烈建议修改：降低可靠性/可维护性/安全性
    MINOR = "minor"        # 🟢 有优化空间：风格/可润色项


SEVERITY_ICON = {Severity.CRITICAL: "🔴", Severity.MAJOR: "🟡", Severity.MINOR: "🟢"}
SEVERITY_PENALTY = {Severity.CRITICAL: 4.0, Severity.MAJOR: 2.0, Severity.MINOR: 0.5}


# ── 数据结构 ──────────────────────────────────────────────────────


@dataclass
class Issue:
    """单条审查问题。"""

    dimension: Dimension
    severity: Severity
    message: str
    location: str = ""       # 形如 "module.py:42" 或 "L42"
    suggestion: str = ""     # 具体可执行的修改方向
    rule_id: str = ""        # 规则标识，便于去重与统计


@dataclass
class DimensionScore:
    """单个维度的评分结果。"""

    dimension: Dimension
    score: float             # 0–10；N/A 时设 None
    weight: float            # 该维度的相对权重（类型适配）
    issues: list[Issue] = field(default_factory=list)
    note: str = ""           # 维度小结
    applicable: bool = True  # 是否可评估（False → 诚实 N/A）

    @property
    def weighted(self) -> float:
        if not self.applicable or self.score is None:
            return 0.0
        return self.score * self.weight


@dataclass
class ReviewReport:
    """一次完整审查的结构化报告。"""

    language: str
    code_type: str
    dimensions: list[DimensionScore]
    issues: list[Issue] = field(default_factory=list)
    type_specific: dict[str, Any] = field(default_factory=dict)
    honest_na: list[str] = field(default_factory=list)
    overall_score: float = 0.0
    grade: str = ""
    summary: str = ""

    @property
    def prioritized_issues(self) -> list[Issue]:
        """按严重度 → 维度顺序排序的问题清单。"""
        order = {Severity.CRITICAL: 0, Severity.MAJOR: 1, Severity.MINOR: 2}
        dim_order = {d: i for i, d in enumerate(Dimension)}
        return sorted(
            self.issues,
            key=lambda i: (order[i.severity], dim_order[i.dimension]),
        )

    @property
    def applicable_dimensions(self) -> list[DimensionScore]:
        return [d for d in self.dimensions if d.applicable and d.score is not None]


# ── 语言与代码类型识别 ────────────────────────────────────────────


_LANG_HINTS = {
    "python": {"py", "pyi"},
    "javascript": {"js", "mjs", "cjs"},
    "typescript": {"ts"},
    "go": {"go"},
    "rust": {"rs"},
    "java": {"java"},
    "cpp": {"cpp", "cc", "cxx", "hpp"},
}


def detect_language(code: str, hint: Optional[str] = None) -> str:
    """识别代码语言。

    hint 可为扩展名（".py"）或语言名；否则按启发式判断。
    返回小写语言标识，无法判断时返回 "generic"。
    """
    if hint:
        h = hint.lower().lstrip(".")
        if h in _LANG_HINTS:
            return h
        for lang, exts in _LANG_HINTS.items():
            if h in exts:
                return lang
        # 允许直接传语言名
        if h in {d for v in _LANG_HINTS.values() for d in v}:
            return h

    c = code
    # Python：def/async def/class 后跟 `name(` 与 `):`（Python 标志性语法）
    if re.search(r"\b(async\s+def|def|class)\s+\w+\s*(\(.*\))?\s*:", c):
        return "python"
    # Go
    if re.search(r"\bfunc \w+", c) and "package " in c:
        return "go"
    # Rust
    if re.search(r"\bfn \w+", c) and ("let mut" in c or "use std" in c or "::" in c):
        return "rust"
    # TypeScript
    if re.search(r":\s*(string|number|boolean|void|any)\b", c) or "interface " in c or re.search(r":\s*\w+\[\]", c):
        return "typescript"
    # JavaScript
    if re.search(r"\b(function|const|let|var)\b", c) or "=>" in c or "console.log" in c or "require(" in c:
        return "javascript"
    # Java
    if "public class" in c or "System.out" in c or "public static void" in c:
        return "java"
    # C++
    if "#include" in c and ("int main" in c or "std::" in c):
        return "cpp"
    return "generic"


def detect_code_type(code: str, language: str) -> str:
    """识别代码类型（业务场景），用于应用类型专属审查标准。

    返回：web_service / data_pipeline / cli / test_suite / library / script
    """
    c = code
    low = c.lower()
    if "def test_" in c or "async def test_" in c or "class Test" in c or \
       "unittest" in c or "pytest" in low or "assertEqual" in c:
        return "test_suite"
    if "fastapi" in low or "flask" in low or "django" in low or "@app.route" in c or "Request" in c or "express" in low:
        return "web_service"
    if "pandas" in low or "pyspark" in low or "spark" in low or "numpy" in low or "dataframe" in low:
        return "data_pipeline"
    if "argparse" in c or "click" in low or "sys.argv" in c or "getopt" in low:
        return "cli"
    if re.search(r"\b(def|class|function|func)\s+\w+", c) and ("return" in c or "export" in c):
        # 含可导出/可复用单元 → 倾向 library；否则 script
        if "if __name__" in c or "def main" in c or "void main" in c:
            return "script"
        return "library"
    return "script"


# ── 权重配置（类型适配）────────────────────────────────────────────

# 维度基础权重（默认 1.0）
_BASE_WEIGHTS: dict[Dimension, float] = {d: 1.0 for d in Dimension}
_BASE_WEIGHTS[Dimension.SECURITY] = 1.2
_BASE_WEIGHTS[Dimension.CORRECTNESS] = 1.2
_BASE_WEIGHTS[Dimension.MAINTAINABILITY] = 1.1

# 代码类型对权重的微调（乘性）
_TYPE_WEIGHT_ADJUST: dict[str, dict[Dimension, float]] = {
    "web_service": {
        Dimension.SECURITY: 1.3, Dimension.CONCURRENCY: 1.2,
        Dimension.OBSERVABILITY: 1.2, Dimension.PERFORMANCE: 1.1,
    },
    "data_pipeline": {
        Dimension.PERFORMANCE: 1.4, Dimension.CONCURRENCY: 1.2,
        Dimension.CORRECTNESS: 1.1,
    },
    "cli": {
        Dimension.DOCUMENTATION: 1.2,
    },
    "test_suite": {
        Dimension.TESTING: 1.6, Dimension.CORRECTNESS: 1.1,
    },
    "library": {
        Dimension.DOCUMENTATION: 1.4, Dimension.MAINTAINABILITY: 1.2,
    },
    "script": {
        Dimension.MAINTAINABILITY: 0.9, Dimension.DOCUMENTATION: 0.8,
    },
}


def _weights_for(code_type: str) -> dict[Dimension, float]:
    """计算某代码类型下各维度的实际权重。"""
    weights = dict(_BASE_WEIGHTS)
    adj = _TYPE_WEIGHT_ADJUST.get(code_type, {})
    for dim, mult in adj.items():
        weights[dim] = round(weights[dim] * mult, 2)
    return weights


# ── 规则检查器 ────────────────────────────────────────────────────


class CodeReviewer:
    """多维加权代码审查引擎。

    Usage::

        reviewer = CodeReviewer()
        report = reviewer.review("def foo(): ...", language="python")
        md = CodeReviewer.to_markdown(report)
    """

    def __init__(self, analyzer: Optional[Any] = None, llm: Optional[Any] = None) -> None:
        """
        Args:
            analyzer: AST 分析器（默认 lazily 构造 jarvis.codex.Analyzer）
            llm: 可选 LLM 后端，用于语义级增强（当前预留接口，不影响规则级结果）
        """
        self._analyzer = analyzer
        self._llm = llm

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def review(
        self,
        code: str,
        language: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> ReviewReport:
        """对代码进行八维加权审查，返回结构化报告。"""
        language = detect_language(code, language)
        code_type = detect_code_type(code, language)
        context = context or {}

        # 1. 收集各维度问题
        raw: dict[Dimension, list[Issue]] = {d: [] for d in Dimension}

        if language == "python":
            self._check_python(code, raw)
        else:
            self._check_generic(code, language, raw)

        # 维度级别、语言无关的规则（安全/可观测/文档等部分适用所有语言）
        self._check_language_agnostic(code, language, raw)
        # 类型专项检查
        type_specific = self._type_specific_checks(code, language, code_type, raw)

        # 2. 计算维度评分（含诚实 N/A）
        weights = _weights_for(code_type)
        dimensions: list[DimensionScore] = []
        honest_na: list[str] = []

        for dim in Dimension:
            issues = raw[dim]
            applicable, note = self._is_applicable(dim, issues, code, language, code_type)
            if not applicable:
                honest_na.append(dim.value)
                dimensions.append(DimensionScore(
                    dimension=dim, score=None, weight=weights[dim],
                    issues=[], note=note, applicable=False,
                ))
                continue
            penalty = sum(SEVERITY_PENALTY[i.severity] for i in issues)
            score = max(0.0, round(10.0 - penalty, 1))
            dimensions.append(DimensionScore(
                dimension=dim, score=score, weight=weights[dim],
                issues=issues, note=note, applicable=True,
            ))

        # 3. 汇总问题 + 加权总分 + 定级
        all_issues = [i for d in dimensions if d.applicable for i in d.issues]
        applicable = [d for d in dimensions if d.applicable and d.score is not None]
        if applicable:
            total_w = sum(d.weight for d in applicable)
            overall = sum(d.weighted for d in applicable) / total_w if total_w else 0.0
            overall = round(overall, 1)
        else:
            overall = 0.0
        grade = self._grade(overall)

        summary = self._summarize(overall, grade, code_type, all_issues, honest_na)

        return ReviewReport(
            language=language,
            code_type=code_type,
            dimensions=dimensions,
            issues=all_issues,
            type_specific=type_specific,
            honest_na=honest_na,
            overall_score=overall,
            grade=grade,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # 适用性判定（诚实 N/A）
    # ------------------------------------------------------------------

    @staticmethod
    def _is_applicable(
        dim: Dimension, issues: list[Issue], code: str, language: str, code_type: str
    ) -> tuple[bool, str]:
        if dim == Dimension.TESTING:
            # 仅当提供了测试套件代码时才评估；否则诚实 N/A
            if code_type != "test_suite" and "def test_" not in code and "assertEqual" not in code:
                return False, "未提供测试代码，测试维度不适用（N/A）。"
            return True, "已基于代码中的断言/测试结构评估。"
        if dim == Dimension.CONCURRENCY:
            has_conc = (
                "async def" in code or "await " in code or "threading" in code
                or "multiprocessing" in code or "asyncio" in code or "Thread(" in code
            )
            if not has_conc:
                return False, "代码无并发/异步结构，并发安全维度不适用（N/A）。"
            return True, "已检查异步阻塞与共享可变状态。"
        return True, ""

    # ------------------------------------------------------------------
    # Python AST 规则
    # ------------------------------------------------------------------

    def _get_analyzer(self):
        if self._analyzer is None:
            from jarvis.codex.analyzer import Analyzer
            self._analyzer = Analyzer()
        return self._analyzer

    def _check_python(self, code: str, raw: dict[Dimension, list[Issue]]) -> None:
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raw[Dimension.CORRECTNESS].append(Issue(
                Dimension.CORRECTNESS, Severity.CRITICAL,
                f"语法错误：{e.msg}",
                location=f"L{e.lineno}" if e.lineno else "",
                suggestion="修复语法错误后再提交。",
                rule_id="py.syntax",
            ))
            return

        lines = code.splitlines()

        for node in ast.walk(tree):
            # ---- 正确性 ----
            if isinstance(node, ast.FunctionDef):
                # 可变默认参数
                for d in node.args.defaults:
                    if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                        raw[Dimension.CORRECTNESS].append(Issue(
                            Dimension.CORRECTNESS, Severity.MAJOR,
                            f"函数 {node.name} 使用了可变默认参数（list/dict/set），"
                            "多次调用会共享状态。",
                            location=f"L{node.lineno}",
                            suggestion="将默认值改为 None，函数体内初始化。",
                            rule_id="py.mutable_default",
                        ))
                # 公开函数缺 docstring
                if not (node.name.startswith("_")) and ast.get_docstring(node) is None:
                    raw[Dimension.DOCUMENTATION].append(Issue(
                        Dimension.DOCUMENTATION, Severity.MINOR,
                        f"函数 {node.name} 缺少 docstring。",
                        location=f"L{node.lineno}",
                        suggestion="为公开函数补充 docstring，说明用途与参数。",
                        rule_id="py.missing_docstring",
                    ))
                # 缺类型注解
                if node.args.args and all(a.annotation is None for a in node.args.args):
                    raw[Dimension.DOCUMENTATION].append(Issue(
                        Dimension.DOCUMENTATION, Severity.MINOR,
                        f"函数 {node.name} 未使用类型注解。",
                        location=f"L{node.lineno}",
                        suggestion="为参数与返回值添加类型注解（如 def f(x: int) -> str）。",
                        rule_id="py.no_type_hints",
                    ))
            elif isinstance(node, ast.ClassDef):
                if ast.get_docstring(node) is None:
                    raw[Dimension.DOCUMENTATION].append(Issue(
                        Dimension.DOCUMENTATION, Severity.MINOR,
                        f"类 {node.name} 缺少 docstring。",
                        location=f"L{node.lineno}",
                        suggestion="为类补充 docstring，说明职责。",
                        rule_id="py.class_no_doc",
                    ))
            elif isinstance(node, ast.Compare):
                # `is` 比较字面量
                for op in node.ops:
                    if isinstance(op, ast.Is):
                        for comp in node.comparators:
                            if isinstance(comp, (ast.Constant,)) and isinstance(comp.value, (int, str, bytes, bool)):
                                raw[Dimension.CORRECTNESS].append(Issue(
                                    Dimension.CORRECTNESS, Severity.MAJOR,
                                    "使用 `is` 比较字面量（如 `x is 5`），"
                                    "`is` 仅用于身份比较，应使用 `==`。",
                                    suggestion="改用 == 进行值比较。",
                                    rule_id="py.is_literal",
                                ))
            elif isinstance(node, ast.Assert):
                # assert 用于生产逻辑（可能被 -O 关闭）
                raw[Dimension.CORRECTNESS].append(Issue(
                    Dimension.CORRECTNESS, Severity.MINOR,
                    "使用 assert 做运行时校验；在 python -O 下会被禁用。",
                    location=f"L{node.lineno}",
                    suggestion="关键校验改用显式 if/raise 异常。",
                    rule_id="py.assert_logic",
                ))
            # ---- 并发安全 ----
            elif isinstance(node, ast.AsyncFunctionDef):
                # 异步函数内出现阻塞调用
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        fn = ast.unparse(sub.func) if hasattr(ast, "unparse") else ""
                        if fn in ("time.sleep", "open") or fn.startswith("os."):
                            raw[Dimension.CONCURRENCY].append(Issue(
                                Dimension.CONCURRENCY, Severity.MAJOR,
                                f"异步函数 {node.name} 内调用阻塞式 {fn}()，"
                                "会阻塞事件循环。",
                                location=f"L{node.lineno}",
                                suggestion="改用异步等价（asyncio.sleep / aiofiles）或在线程池执行。",
                                rule_id="py.async_blocking",
                            ))
            # ---- 可观测性 ----
            elif isinstance(node, ast.ExceptHandler):
                if node.name is None and node.type is None:
                    raw[Dimension.OBSERVABILITY].append(Issue(
                        Dimension.OBSERVABILITY, Severity.MAJOR,
                        "使用裸 `except:`，吞掉所有异常且无日志。",
                        location=f"L{node.lineno}",
                        suggestion="捕获具体异常并记录日志，避免静默失败。",
                        rule_id="py.bare_except",
                    ))
                else:
                    # 存在 except 但无 logging/print → 静默吞异常
                    body_src = ast.unparse(node.body) if hasattr(ast, "unparse") else ""
                    if "log" not in body_src and "print" not in body_src and "raise" not in body_src:
                        raw[Dimension.OBSERVABILITY].append(Issue(
                            Dimension.OBSERVABILITY, Severity.MINOR,
                            "异常被捕获但未记录日志也未重新抛出，难以排障。",
                            location=f"L{node.lineno}",
                            suggestion="在 except 块中记录日志（logger.exception）。",
                            rule_id="py.silent_except",
                        ))

        # ---- 模块级文档/可维护 ----
        if not (code.strip().startswith('"""') or ast.get_docstring(tree) is not None):
            # 第一句非 docstring
            if not code.lstrip().startswith('"""') and not code.lstrip().startswith("'''"):
                raw[Dimension.DOCUMENTATION].append(Issue(
                    Dimension.DOCUMENTATION, Severity.MINOR,
                    "模块缺少模块级 docstring。",
                    suggestion="在文件顶部补充模块说明。",
                    rule_id="py.module_doc",
                ))

        # ---- 性能：字符串在循环中 += ----
        if re.search(r"\+=\s*[\"']", code) or re.search(r"\w+\s*\+=\s*\w+", code):
            # 粗粒度：检测循环内字符串拼接
            if re.search(r"for .*:\s*\n\s*\w+\s*\+=\s*[\"']", code):
                raw[Dimension.PERFORMANCE].append(Issue(
                    Dimension.PERFORMANCE, Severity.MINOR,
                    "循环内进行字符串 `+=` 拼接，复杂度为 O(n^2)。",
                    suggestion="改用 list.append 后 ''.join(...)。",
                    rule_id="py.loop_str_cat",
                ))

    # ------------------------------------------------------------------
    # 跨语言规则（正则）
    # ------------------------------------------------------------------

    def _check_language_agnostic(self, code: str, language: str, raw: dict[Dimension, list[Issue]]) -> None:
        lines = code.splitlines()
        for idx, line in enumerate(lines, start=1):
            loc = f"L{idx}"
            low = line.lower()
            stripped = line.strip()

            # ---- 安全：硬编码密钥 ----
            if re.search(r"(password|passwd|secret|api[_-]?key|token|access[_-]?key)\s*[:=]", low) \
                    and re.search(r"[\"'](.*)[\"']", line) \
                    and not stripped.startswith("#"):
                # 排除明显占位
                val = re.search(r"[\"']([^\"']+)[\"']", line)
                if val and val.group(1).lower() not in ("", "your_key_here", "<key>", "none", "none", "changeme"):
                    raw[Dimension.SECURITY].append(Issue(
                        Dimension.SECURITY, Severity.CRITICAL,
                        f"疑似硬编码密钥/口令：{stripped[:60]}",
                        location=loc,
                        suggestion="将密钥移出代码，改用环境变量或密钥管理服务。",
                        rule_id="sec.hardcoded_secret",
                    ))

            # ---- 安全：危险函数 ----
            if re.search(r"\b(eval|exec)\s*\(", line) and "os.environ" not in line:
                raw[Dimension.SECURITY].append(Issue(
                    Dimension.SECURITY, Severity.CRITICAL,
                    f"使用 {stripped[:40]} —— eval/exec 执行动态代码，存在代码注入风险。",
                    location=loc,
                    suggestion="避免 eval/exec；如需解析，使用 ast.literal_eval 或专用解析器。",
                    rule_id="sec.eval_exec",
                ))
            if re.search(r"subprocess\.(call|run|Popen).*shell\s*=\s*True", line) or \
               (re.search(r"os\.system\s*\(", line)):
                raw[Dimension.SECURITY].append(Issue(
                    Dimension.SECURITY, Severity.MAJOR,
                    "调用 shell=True / os.system，命令含拼接时存在命令注入风险。",
                    location=loc,
                    suggestion="避免 shell=True；使用参数列表形式传参。",
                    rule_id="sec.shell_injection",
                ))
            if re.search(r"pickle\.loads?", line) or re.search(r"yaml\.load\s*\(", line):
                raw[Dimension.SECURITY].append(Issue(
                    Dimension.SECURITY, Severity.MAJOR,
                    "使用 pickle.loads / yaml.load（非 SafeLoader），反序列化不可信数据会执行任意代码。",
                    location=loc,
                    suggestion="改用 json；yaml 使用 yaml.safe_load。",
                    rule_id="sec.unsafe_deser",
                ))
            if re.search(r"requests\.(get|post).*verify\s*=\s*False", line) or \
               re.search(r"ssl\.create_default_context\(\)\s*,\s*check_hostname\s*=\s*False", line):
                raw[Dimension.SECURITY].append(Issue(
                    Dimension.SECURITY, Severity.MAJOR,
                    "关闭了 TLS 证书校验（verify=False），存在中间人攻击风险。",
                    location=loc,
                    suggestion="保留证书校验；如需自签证书，将其加入信任链。",
                    rule_id="sec.no_verify",
                ))
            if re.search(r"(md5|sha1)\s*\(", line):
                raw[Dimension.SECURITY].append(Issue(
                    Dimension.SECURITY, Severity.MINOR,
                    "使用 MD5/SHA1 等弱哈希（用于安全场景时不推荐）。",
                    location=loc,
                    suggestion="安全用途改用 SHA-256 或 bcrypt/argon2。",
                    rule_id="sec.weak_hash",
                ))
            if re.search(r"sql\s*=\s*[\"'].*\%s.*[\"']\s*%\s*|\.format\(.*\)\.format", line) or \
               re.search(r"f[\"'].*SELECT.*\{", line):
                raw[Dimension.SECURITY].append(Issue(
                    Dimension.SECURITY, Severity.MAJOR,
                    "SQL 语句疑似通过字符串拼接/格式化构造，存在 SQL 注入风险。",
                    location=loc,
                    suggestion="使用参数化查询（占位符 + 参数绑定）。",
                    rule_id="sec.sql_injection",
                ))

            # ---- 可观测性：print 用于产出 ----
            if re.search(r"\bprint\s*\(", line) and "debug" not in low and "logger" not in low:
                # 仅当明显用于生产输出（非调试）时标记轻微
                raw[Dimension.OBSERVABILITY].append(Issue(
                    Dimension.OBSERVABILITY, Severity.MINOR,
                    "使用 print 输出，建议改用 logging 以便控制级别与采集。",
                    location=loc,
                    suggestion="引入 logging 模块，按级别输出。",
                    rule_id="obs.print_usage",
                ))

        # ---- 可维护性：过长的裸函数（通用启发）----
        # 由 analyzer 提供更精确结果；此处仅做兜底（python 已在 AST 内覆盖）

    # ------------------------------------------------------------------
    # 非 Python（通用）规则
    # ------------------------------------------------------------------

    def _check_generic(self, code: str, language: str, raw: dict[Dimension, list[Issue]]) -> None:
        # 非 Python：复用语言无关规则 + 基础启发
        # 正确性：明显的语法不可用静态保证，仅做注释/括号配对粗查
        if code.count("(") != code.count(")"):
            raw[Dimension.CORRECTNESS].append(Issue(
                Dimension.CORRECTNESS, Severity.MAJOR,
                "括号 '(' 与 ')' 数量不匹配，可能存在语法错误。",
                suggestion="检查并补全括号。",
                rule_id="gen.paren_mismatch",
            ))
        if code.count("{") != code.count("}"):
            raw[Dimension.CORRECTNESS].append(Issue(
                Dimension.CORRECTNESS, Severity.MAJOR,
                "大括号 '{' 与 '}' 数量不匹配。",
                suggestion="检查并补全括号。",
                rule_id="gen.brace_mismatch",
            ))
        # 注：安全/可观测/文档的跨语言规则已在 _check_language_agnostic 内覆盖

    # ------------------------------------------------------------------
    # 类型专属检查
    # ------------------------------------------------------------------

    def _type_specific_checks(
        self, code: str, language: str, code_type: str, raw: dict[Dimension, list[Issue]]
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        low = code.lower()

        if code_type == "web_service":
            # 输入校验
            has_validation = any(k in low for k in ["pydantic", "validate", "schema", "marshmallow", "validator"])
            result["input_validation"] = "present" if has_validation else "missing"
            if not has_validation:
                raw[Dimension.SECURITY].append(Issue(
                    Dimension.SECURITY, Severity.MAJOR,
                    "Web 服务未检测到输入校验（pydantic/validator 等），易引发注入/越权。",
                    suggestion="为请求体引入 schema 校验（如 Pydantic）。",
                    rule_id="type.ws.no_validation",
                ))
            # 鉴权
            has_auth = any(k in low for k in ["authorization", "auth", "token", "jwt", "oauth", "api_key"])
            result["auth"] = "present" if has_auth else "missing"
            if not has_auth:
                raw[Dimension.SECURITY].append(Issue(
                    Dimension.SECURITY, Severity.MINOR,
                    "未检测到鉴权逻辑，确认是否为公开端点。",
                    suggestion="为受保护端点加入鉴权中间件。",
                    rule_id="type.ws.no_auth",
                ))

        elif code_type == "data_pipeline":
            # 幂等 / 失败重试
            has_idempotent = any(k in low for k in ["idempotent", "retry", "checkpoint", "commit", "atomic"])
            result["fault_tolerance"] = "present" if has_idempotent else "missing"
            if not has_idempotent:
                raw[Dimension.CORRECTNESS].append(Issue(
                    Dimension.CORRECTNESS, Severity.MINOR,
                    "数据管道未检测到幂等/重试/检查点机制，失败重跑可能重复或丢数据。",
                    suggestion="加入幂等键、重试与检查点。",
                    rule_id="type.dp.no_resilience",
                ))

        elif code_type == "cli":
            has_help = "--help" in low or "help=" in low or "usage" in low
            result["help_text"] = "present" if has_help else "missing"
            if not has_help:
                raw[Dimension.DOCUMENTATION].append(Issue(
                    Dimension.DOCUMENTATION, Severity.MINOR,
                    "CLI 未提供 --help / usage 说明。",
                    suggestion="为参数添加 help 文本并支持 --help。",
                    rule_id="type.cli.no_help",
                ))

        elif code_type == "test_suite":
            has_assert = "assert" in low or "self.assert" in low or "expect" in low
            result["assertions"] = "present" if has_assert else "missing"
            if not has_assert:
                raw[Dimension.TESTING].append(Issue(
                    Dimension.TESTING, Severity.MAJOR,
                    "测试套件中未发现断言，测试不会验证任何行为。",
                    suggestion="为每个测试用例添加明确断言。",
                    rule_id="type.test.no_assert",
                ))

        return result

    # ------------------------------------------------------------------
    # 评分与定级
    # ------------------------------------------------------------------

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 9.0:
            return "S"
        if score >= 8.0:
            return "A"
        if score >= 7.0:
            return "B"
        if score >= 6.0:
            return "C"
        return "D"

    @staticmethod
    def _summarize(overall: float, grade: str, code_type: str, issues: list[Issue], na: list[str]) -> str:
        n_crit = sum(1 for i in issues if i.severity == Severity.CRITICAL)
        n_major = sum(1 for i in issues if i.severity == Severity.MAJOR)
        n_minor = sum(1 for i in issues if i.severity == Severity.MINOR)
        parts = [
            f"综合评分 {overall}/10（等级 {grade}），代码类型={code_type}。",
            f"共发现 🔴{n_crit} 🟡{n_major} 🟢{n_minor} 条问题。",
        ]
        if na:
            parts.append(f"诚实标注不适用维度：{', '.join(na)}。")
        if grade in ("S", "A"):
            parts.append("整体质量良好，可进入下一步。")
        elif grade in ("B", "C"):
            parts.append("存在若干可优化项，建议修改后合并。")
        else:
            parts.append("存在严重问题，建议优先修复再评审。")
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Markdown 报告
    # ------------------------------------------------------------------

    @staticmethod
    def to_markdown(report: ReviewReport) -> str:
        """将报告渲染为结构化 Markdown（兼容 script-multi-review 报告风格）。"""
        L: list[str] = []
        L.append("## 代码多维审查报告")
        L.append("")
        L.append(f"- **语言**：{report.language}")
        L.append(f"- **代码类型**：{report.code_type}")
        L.append(f"- **综合评分**：**{report.overall_score} / 10**（等级 **{report.grade}**）")
        if report.honest_na:
            L.append(f"- **诚实 N/A 维度**：{', '.join(report.honest_na)}")
        L.append("")

        # 八维雷达
        L.append("### 八维评分")
        L.append("")
        L.append("| 维度 | 评分 | 权重 | 问题数 |")
        L.append("|------|------|------|--------|")
        for d in report.dimensions:
            if not d.applicable:
                L.append(f"| {d.dimension.value} | N/A | ×{d.weight} | — |")
            else:
                L.append(f"| {d.dimension.value} | {d.score} | ×{d.weight} | {len(d.issues)} |")
        L.append("")

        # 问题清单（按严重度排序）
        L.append("### 问题清单（按严重度排序）")
        L.append("")
        if not report.prioritized_issues:
            L.append("✅ 未发现明显问题。")
        else:
            L.append("| 严重度 | 维度 | 定位 | 问题描述 | 修改建议 |")
            L.append("|--------|------|------|----------|----------|")
            for it in report.prioritized_issues:
                loc = it.location or "—"
                sugg = it.suggestion or "—"
                msg = it.message.replace("|", "\\|")
                L.append(
                    f"| {SEVERITY_ICON[it.severity]} {it.severity.value} | "
                    f"{it.dimension.value} | {loc} | {msg} | {sugg} |"
                )
        L.append("")

        # 类型专项
        if report.type_specific:
            L.append("### 类型专项检查")
            L.append("")
            for k, v in report.type_specific.items():
                L.append(f"- **{k}**：{v}")
            L.append("")

        # 优先修改 Top N
        if report.prioritized_issues:
            L.append("### 优先修改清单（Top 10）")
            L.append("")
            for n, it in enumerate(report.prioritized_issues[:10], 1):
                L.append(
                    f"{n}. 【{SEVERITY_ICON[it.severity]} {it.severity.value}】"
                    f"{it.dimension.value} @ {it.location or '—'}：{it.message}"
                )
            L.append("")

        L.append("### 综合结论")
        L.append("")
        L.append(report.summary)
        L.append("")
        return "\n".join(L)
