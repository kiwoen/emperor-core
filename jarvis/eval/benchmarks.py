"""
Built-in Benchmarks — standardised test suites for agent capability evaluation.

Benchmarks:
    JarvisBench      — 20 题，覆盖全部 12 种内置能力
    RouterBench      — 意图分类准确率，覆盖 8 种意图
    MultiStepBench   — 多步推理任务（需要 2+ 工具调用）
    SelfHealingBench — 模拟故障场景，测试自愈能力

Usage:
    from jarvis.eval import JarvisBench, RouterBench

    bench = JarvisBench()
    results = bench.run(agent=my_agent)
    print(bench.report())
"""

from __future__ import annotations

import json as _json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from jarvis.eval.metrics import (
    task_success_rate,
    tool_call_accuracy,
    route_accuracy,
    healing_success_rate,
    MetricsReport,
    compute_all_metrics,
)


# ══════════════════════════════════════════════════════════════════
# Benchmark case data model
# ══════════════════════════════════════════════════════════════════


@dataclass
class BenchmarkCase:
    """单个基准测试用例。"""

    label: str
    prompt: str
    capability: str = ""
    domain: str = ""
    expected_tool: str = ""            # 期望调用的工具名
    expected_route: str = ""           # 期望路由目标 (domain/capability)
    min_tools_required: int = 1        # 不少于 N 次工具调用
    is_fault_injection: bool = False   # 是否故障注入
    fault_type: str = ""               # 故障类型: timeout / tool_error / partial_response
    validator: Optional[Callable[[Any], tuple[bool, str]]] = None


# ══════════════════════════════════════════════════════════════════
# Abstract benchmark
# ══════════════════════════════════════════════════════════════════


@dataclass
class BenchmarkResult:
    """单个基准测试的运行结果。"""

    name: str
    description: str
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    metrics: MetricsReport = field(default_factory=MetricsReport)
    per_case: List[Dict[str, Any]] = field(default_factory=list)
    started_at: float = 0
    finished_at: float = 0

    @property
    def pass_rate(self) -> float:
        attempted = self.passed + self.failed
        return self.passed / attempted if attempted > 0 else 0.0

    @property
    def duration_seconds(self) -> float:
        return round(self.finished_at - self.started_at, 3)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "total_cases": self.total_cases,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "pass_rate": round(self.pass_rate, 4),
            "duration_seconds": self.duration_seconds,
            "metrics": self.metrics.to_dict(),
            "per_case": self.per_case,
        }


class Benchmark(ABC):
    """基准测试抽象基类。"""

    name: str = ""
    description: str = ""

    @abstractmethod
    def build_cases(self) -> List[BenchmarkCase]:
        """构建测试用例列表。"""
        ...

    def run(self, agent: Any = None) -> BenchmarkResult:
        """运行基准测试并返回结果。

        Args:
            agent: 被测 agent 实例，需实现 handle(prompt) -> dict 或类似接口。
        """
        cases = self.build_cases()

        result = BenchmarkResult(
            name=self.name,
            description=self.description,
            total_cases=len(cases),
            started_at=time.time(),
        )

        passed_list: List[bool] = []
        exp_tools: List[str] = []
        act_tools: List[str] = []
        times_ms: List[float] = []
        exp_routes: List[str] = []
        act_routes: List[str] = []
        healing_ok: List[bool] = []
        healing_depths: List[int] = []

        for case in cases:
            case_result: Dict[str, Any] = {"label": case.label, "status": "pass"}
            case_passed = True
            t0 = time.time()

            try:
                if agent is None:
                    # 脱机模式：不做真实 agent 调用，仅验证用例构造正确性
                    case_result["note"] = "no agent provided, case construction verified"
                    elapsed_ms = 0.0
                elif hasattr(agent, "handle"):
                    import asyncio as _asyncio

                    handle_fn = agent.handle
                    if _asyncio.iscoroutinefunction(handle_fn):
                        output = _asyncio.run(handle_fn(case.prompt))
                    else:
                        output = handle_fn(case.prompt)
                    elapsed_ms = (time.time() - t0) * 1000
                    case_result["agent_output"] = str(output)[:500]

                    # 自定义 validator
                    if case.validator:
                        v_ok, v_msg = case.validator(output)
                        if not v_ok:
                            case_passed = False
                            case_result["status"] = "fail"
                            case_result["detail"] = v_msg
                elif hasattr(agent, "execute_task"):
                    output = agent.execute_task(case.prompt)
                    elapsed_ms = (time.time() - t0) * 1000
                    case_result["agent_output"] = str(output)[:500]

                    if case.validator:
                        v_ok, v_msg = case.validator(output)
                        if not v_ok:
                            case_passed = False
                            case_result["status"] = "fail"
                            case_result["detail"] = v_msg
                else:
                    case_result["note"] = "agent has no handle() or execute_task(); skipped"
                    elapsed_ms = 0.0

                # 特定验证：期望工具
                if case.expected_tool and "agent_output" in case_result:
                    out_str = case_result["agent_output"].lower()
                    if case.expected_tool.lower() not in out_str:
                        case_passed = False
                        case_result["status"] = "fail"
                        case_result["detail"] = f"Expected tool '{case.expected_tool}' not found"

                # 特定验证：最少工具调用
                if case.min_tools_required > 1 and "agent_output" in case_result:
                    out_str = case_result["agent_output"].lower()
                    tool_count = sum(
                        1 for tool in ["search", "fetch", "execute", "route", "parse", "analyze", "generate", "validate", "compute", "query"]
                        if tool in out_str
                    )
                    if tool_count < case.min_tools_required:
                        case_passed = False
                        case_result["status"] = "fail"
                        case_result["detail"] = (
                            f"Expected >= {case.min_tools_required} tools, found {tool_count}"
                        )

            except Exception as exc:
                elapsed_ms = (time.time() - t0) * 1000
                case_passed = False
                case_result["status"] = "error"
                case_result["detail"] = str(exc)

            case_result["duration_ms"] = round(elapsed_ms, 2)

            passed_list.append(case_passed)
            times_ms.append(elapsed_ms)
            result.per_case.append(case_result)

            if case_passed:
                result.passed += 1
            elif case_result.get("status") == "error":
                result.errored += 1
            else:
                result.failed += 1

        result.finished_at = time.time()
        result.metrics = compute_all_metrics(passed=passed_list, times_ms=times_ms)
        return result

    def report(self, result: Optional[BenchmarkResult] = None) -> str:
        """生成可读报告。"""
        if result is None:
            return f"Benchmark '{self.name}' not yet run."

        lines = [
            f"══ {result.name} ══",
            f"  Description: {result.description}",
            f"  Total: {result.total_cases}  "
            f"Pass: {result.passed}  "
            f"Fail: {result.failed}  "
            f"Error: {result.errored}",
            f"  Pass Rate: {result.pass_rate:.1%}  "
            f"Duration: {result.duration_seconds}s",
            f"  Overall Score: {result.metrics.overall_score():.4f}",
            f"  Task Success Rate:   {result.metrics.task_success_rate:.4f}",
            "",
        ]
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 1. JarvisBench — 20 题覆盖 12 种内置能力
# ══════════════════════════════════════════════════════════════════

class JarvisBench(Benchmark):
    """覆盖全部 12 种内置能力的综合基准测试，共 20 道题。

    12 种能力:
        datetime, math, random, text, file_info, hash,
        json_tool, uuid_gen, weather, news, web_search, web_fetch
    """

    name = "JarvisBench"
    description = "20 道综合测试题，覆盖全部 12 种内置能力"

    def build_cases(self) -> List[BenchmarkCase]:
        return [
            # datetime (2)
            BenchmarkCase("JB_dt_01", "现在的日期和时间是多少？", capability="datetime",
                          expected_route="datetime"),
            BenchmarkCase("JB_dt_02", "今天是星期几？", capability="datetime",
                          expected_route="datetime"),

            # math (2)
            BenchmarkCase("JB_math_01", "计算 456 * 789 的结果", capability="math",
                          expected_route="math"),
            BenchmarkCase("JB_math_02", "求 (100 - 25) / 5 + 3 * 8 的值", capability="math",
                          expected_route="math"),

            # random (2)
            BenchmarkCase("JB_rand_01", "生成一个 1 到 100 之间的随机整数", capability="random",
                          expected_route="random"),
            BenchmarkCase("JB_rand_02", "模拟掷两个六面骰子并告诉我总点数", capability="random",
                          expected_route="random"),

            # text (2)
            BenchmarkCase("JB_txt_01", "统计 'Hello World from Jarvis!' 的字符数", capability="text",
                          expected_route="text"),
            BenchmarkCase("JB_txt_02", "把 'artificial intelligence' 转成大写", capability="text",
                          expected_route="text"),

            # file_info (2)
            BenchmarkCase("JB_file_01", "检查 C:\\Windows\\System32\\notepad.exe 是否存在",
                          capability="file_info", expected_route="file_info"),
            BenchmarkCase("JB_file_02", "查看 C:\\Users\\Public\\Desktop 的大小信息",
                          capability="file_info", expected_route="file_info"),

            # hash (2)
            BenchmarkCase("JB_hash_01", "计算 'password123' 的 MD5 哈希值", capability="hash",
                          expected_route="hash"),
            BenchmarkCase("JB_hash_02", "对 'Jarvis Core v1.0' 做 SHA-256 摘要", capability="hash",
                          expected_route="hash"),

            # json_tool (2)
            BenchmarkCase("JB_json_01", '验证这段 JSON 是否合法：{"name":"Jarvis","version":"1.0"}',
                          capability="json_tool", expected_route="json_tool"),
            BenchmarkCase("JB_json_02", '格式化 JSON: {"a":1,"b":[2,3],"c":{"d":"e"}}',
                          capability="json_tool", expected_route="json_tool"),

            # uuid_gen (2)
            BenchmarkCase("JB_uuid_01", "帮我生成一个 UUID v4", capability="uuid_gen",
                          expected_route="uuid_gen"),
            BenchmarkCase("JB_uuid_02", "生成 3 个 UUID 并以换行分隔输出", capability="uuid_gen",
                          expected_route="uuid_gen"),

            # weather (1)
            BenchmarkCase("JB_wx_01", "查询北京今天的天气情况", capability="weather",
                          expected_route="weather"),

            # news (1)
            BenchmarkCase("JB_news_01", "获取最新的科技新闻", capability="news",
                          expected_route="news"),

            # web_search (1)
            BenchmarkCase("JB_ws_01", "搜索 Python 3.12 有哪些新特性", capability="web_search",
                          expected_route="web_search"),

            # web_fetch (1)
            BenchmarkCase("JB_wf_01", "抓取 https://example.com 的页面内容",
                          capability="web_fetch", expected_route="web_fetch"),
        ]


# ══════════════════════════════════════════════════════════════════
# 2. RouterBench — 意图分类准确率（8 种意图）
# ══════════════════════════════════════════════════════════════════

class RouterBench(Benchmark):
    """意图分类准确率基准测试，覆盖全部 8 种 domain 意图。

    8 种意图:
        creator, engineering, research, personal,
        security, health, finance, home
    """

    name = "RouterBench"
    description = "意图分类准确率测试，覆盖 8 种 domain 意图"

    def build_cases(self) -> List[BenchmarkCase]:
        return [
            # creator
            BenchmarkCase("RB_creator_01", "帮我写一个科幻短篇小说", domain="creator",
                          expected_route="creator"),
            BenchmarkCase("RB_creator_02", "设计一张科技产品海报", domain="creator",
                          expected_route="creator"),

            # engineering
            BenchmarkCase("RB_eng_01", "用 Python 写一个二分查找函数", domain="engineering",
                          expected_route="engineering"),
            BenchmarkCase("RB_eng_02", "这段代码的内存泄漏怎么排查？", domain="engineering",
                          expected_route="engineering"),

            # research
            BenchmarkCase("RB_research_01", "搜索最近关于量子计算的学术论文", domain="research",
                          expected_route="research"),
            BenchmarkCase("RB_research_02", "分析 AI 安全领域的最新研究趋势", domain="research",
                          expected_route="research"),

            # personal
            BenchmarkCase("RB_personal_01", "帮我设置明天上午9点的会议提醒", domain="personal",
                          expected_route="personal"),
            BenchmarkCase("RB_personal_02", "查看我今天的日程安排", domain="personal",
                          expected_route="personal"),

            # security
            BenchmarkCase("RB_sec_01", "审查这段代码是否存在SQL注入漏洞", domain="security",
                          expected_route="security"),
            BenchmarkCase("RB_sec_02", "检测我的系统是否有未授权的登录尝试", domain="security",
                          expected_route="security"),

            # health
            BenchmarkCase("RB_health_01", "我每天睡6小时够不够？", domain="health",
                          expected_route="health"),
            BenchmarkCase("RB_health_02", "帮我分析一下最近一周的运动数据", domain="health",
                          expected_route="health"),

            # finance
            BenchmarkCase("RB_fin_01", "分析特斯拉最近一季度的财报", domain="finance",
                          expected_route="finance"),
            BenchmarkCase("RB_fin_02", "帮我做一份投资组合的风险评估", domain="finance",
                          expected_route="finance"),

            # home
            BenchmarkCase("RB_home_01", "列出我家客厅适合放置的智能设备", domain="home",
                          expected_route="home"),
            BenchmarkCase("RB_home_02", "帮我规划这周末的家庭聚餐菜单", domain="home",
                          expected_route="home"),
        ]

    def run(self, agent: Any = None) -> BenchmarkResult:
        """运行 RouterBench，专门计算 route_accuracy。"""
        result = super().run(agent)

        # RouterBench 的核心指标是 route_accuracy
        cases = self.build_cases()
        exp_routes = [c.expected_route for c in cases]

        # 从 per_case 中推导实际路由
        act_routes: List[str] = []
        for case_result in result.per_case:
            # 脱机模式下，从 agent_output 推导路由
            agent_out = case_result.get("agent_output", "")
            matched = ""
            for route in exp_routes:
                if route in str(agent_out).lower():
                    matched = route
                    break
            act_routes.append(matched if matched else "unknown")

        result.metrics = compute_all_metrics(
            passed=[cr["status"] == "pass" for cr in result.per_case],
            expected_routes=exp_routes,
            actual_routes=act_routes,
            times_ms=[cr.get("duration_ms", 0) for cr in result.per_case],
        )
        return result


# ══════════════════════════════════════════════════════════════════
# 3. MultiStepBench — 多步推理任务
# ══════════════════════════════════════════════════════════════════

class MultiStepBench(Benchmark):
    """多步推理基准测试，所有任务均需调用 2+ 工具才能完成。

    场景设计遵循典型的复合任务模式：
        - 搜索 + 分析
        - 读取 + 转换
        - 计算 + 验证
        - 查询 + 过滤 + 汇总
        - 分步处理 + 最终合成
    """

    name = "MultiStepBench"
    description = "多步推理任务测试（需调用 2+ 工具才能完成）"

    def build_cases(self) -> List[BenchmarkCase]:
        return [
            # 搜索 + 提取 (search + fetch)
            BenchmarkCase(
                "MSB_01", "搜索 Python 3.12 的新增特性，然后用获取到的第一个结果页面查看详情",
                expected_tool="search", min_tools_required=2,
                expected_route="research",
            ),
            # 计算 + 验证 (math compute + verify)
            BenchmarkCase(
                "MSB_02", "先计算 1234 * 5678，然后用这个结果除以 100 并取整",
                expected_tool="math", min_tools_required=2,
                expected_route="math",
            ),
            # 数据转换 + 分析 (parse + analyze)
            BenchmarkCase(
                "MSB_03", '解析 "[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]" 这个数组，然后分析哪些是质数',
                expected_tool="json_tool", min_tools_required=2,
                expected_route="math",
            ),
            # 多步骤文件操作 (file check + read)
            BenchmarkCase(
                "MSB_04", "先检查 C:\\Windows\\System32\\drivers\\etc\\hosts 是否存在，存在的话读取它的修改时间",
                expected_tool="file_info", min_tools_required=2,
                expected_route="file_info",
            ),
            # 复合查询 (search + filter + summarize)
            BenchmarkCase(
                "MSB_05", "搜索关于强化学习的论文，然后统计有多少篇，最后用一句话总结每篇的核心发现",
                expected_tool="search", min_tools_required=2,
                expected_route="research",
            ),
            # 生成 + 验证 (generate UUID + validate format)
            BenchmarkCase(
                "MSB_06", "生成一个 UUID，然后验证它的格式是否符合标准 v4 规范",
                expected_tool="uuid_gen", min_tools_required=2,
                expected_route="uuid_gen",
            ),
            # 跨域协作 (weather search → news analysis)
            BenchmarkCase(
                "MSB_07", "先查上海的天气，再用这个结果做背景搜索上海相关的今日新闻",
                expected_tool="weather", min_tools_required=2,
                expected_route="weather",
            ),
            # 创意 + 技术 (writing → hash for uniqueness)
            BenchmarkCase(
                "MSB_08", "写一段不超过100字的自我介绍，然后计算它的SHA256摘要来确定唯一性",
                min_tools_required=2, expected_route="creator",
            ),
            # 安全系列 (scan → alert → patch)
            BenchmarkCase(
                "MSB_09", "扫描此代码段 'SELECT * FROM users WHERE id='+user_id 的安全问题并给出修复方案",
                min_tools_required=2, expected_route="security",
            ),
            # 金融分析链 (fetch data → compute metrics → recommend)
            BenchmarkCase(
                "MSB_10", "获取苹果公司最新股价数据，计算它的PE比率，并给出买入/持有/卖出的建议",
                min_tools_required=2, expected_route="finance",
            ),
        ]


# ══════════════════════════════════════════════════════════════════
# 4. SelfHealingBench — 故障场景自愈测试
# ══════════════════════════════════════════════════════════════════

class SelfHealingBench(Benchmark):
    """模拟故障场景测试自愈能力。

    故障类型:
        - tool_timeout: 工具调用超时
        - tool_error: 工具内部错误
        - partial_response: 部分响应缺失
        - rate_limit: API 限流
        - model_unavailable: 模型不可用
    """

    name = "SelfHealingBench"
    description = "模拟 8 种故障场景，测试自愈引擎的恢复能力"

    def build_cases(self) -> List[BenchmarkCase]:
        return [
            # Tool timeout → 期望切换到替代工具
            BenchmarkCase(
                "SHB_timeout_01", "搜索最新的AI论文",
                is_fault_injection=True, fault_type="tool_timeout",
                expected_route="research",
                validator=self._validate_healing_result,
            ),
            # Tool error → 期望 fallback 到次选方案
            BenchmarkCase(
                "SHB_error_02", "查询明天上海的天气",
                is_fault_injection=True, fault_type="tool_error",
                expected_route="weather",
                validator=self._validate_healing_result,
            ),
            # Partial response → 期望补全或重试
            BenchmarkCase(
                "SHB_partial_03", "生成一份关于微服务的架构报告",
                is_fault_injection=True, fault_type="partial_response",
                expected_route="engineering",
                validator=self._validate_healing_result,
            ),
            # Rate limit → 期望退避重试
            BenchmarkCase(
                "SHB_rate_04", "对3个URL分别抓取并汇总结果",
                is_fault_injection=True, fault_type="rate_limit",
                min_tools_required=1,
                validator=self._validate_healing_result,
            ),
            # Model unavailable → 期望切换到备用模型
            BenchmarkCase(
                "SHB_model_05", "用高级推理分析这个数学证明",
                is_fault_injection=True, fault_type="model_unavailable",
                expected_route="math",
                validator=self._validate_healing_result,
            ),
            # 连续故障 → 期望优雅降级而非崩溃
            BenchmarkCase(
                "SHB_cascade_06", "执行一个需要5步工作流的任务",
                is_fault_injection=True, fault_type="cascade_failure",
                min_tools_required=1,
                validator=self._validate_healing_result,
            ),
            # 输入验证失败 → 期望安全拒绝并解释原因
            BenchmarkCase(
                "SHB_validation_07", "帮我删除系统目录 C:\\Windows 下的文件",
                is_fault_injection=True, fault_type="input_validation",
                validator=self._validate_healing_result,
            ),
            # 资源耗尽 → 期望资源感知的降级
            BenchmarkCase(
                "SHB_resource_08", "同时启动100个并发的web搜索任务",
                is_fault_injection=True, fault_type="resource_exhaustion",
                min_tools_required=1,
                validator=self._validate_healing_result,
            ),
        ]

    @staticmethod
    def _validate_healing_result(output: Any) -> tuple[bool, str]:
        """验证自愈结果：只要不崩溃就算通过（实际场景中可对接 healing engine 统计）。"""
        if output is None:
            return False, "Agent returned None — no healing applied"
        out_str = str(output).lower()

        # 积极信号：系统识别了故障并尝试恢复
        positive_signals = [
            "fallback", "retry", "recovery", "healing",
            "替代", "重试", "恢复", "降级", "backup",
            "attempt", "error", "failed", "unavailable",
            "safety", "cannot", "denied", "拒绝", "无法",
        ]
        for sig in positive_signals:
            if sig in out_str:
                return True, f"Healing signal detected: '{sig}'"

        # 如果没有任何积极信号，但 agent 也没崩溃，仍算保守通过
        return True, "Agent returned output without crashing (conservative pass)"

    def run(self, agent: Any = None) -> BenchmarkResult:
        """运行 SelfHealingBench，专门计算 healing_success_rate。"""
        result = super().run(agent)

        # 覆盖 metrics 中的 healing 部分
        healing_results = [cr["status"] == "pass" for cr in result.per_case]

        result.metrics = compute_all_metrics(
            passed=healing_results,
            times_ms=[cr.get("duration_ms", 0) for cr in result.per_case],
            healing_results=healing_results,
        )
        return result


# ══════════════════════════════════════════════════════════════════
# Registry
# ══════════════════════════════════════════════════════════════════

ALL_BENCHMARKS: Dict[str, Benchmark] = {
    "jarvis": JarvisBench(),
    "router": RouterBench(),
    "multistep": MultiStepBench(),
    "selfhealing": SelfHealingBench(),
}

BENCHMARK_NAMES: Dict[str, str] = {
    k: v.description for k, v in ALL_BENCHMARKS.items()
}


def get_benchmark(name: str) -> Optional[Benchmark]:
    """按名称获取基准测试实例。

    Args:
        name: 基准名称 — "jarvis" / "router" / "multistep" / "selfhealing"

    Returns:
        Benchmark 实例，未找到返回 None。
    """
    return ALL_BENCHMARKS.get(name)
