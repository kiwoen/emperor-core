"""Tests for the master task router (任务路由分发)."""
import pytest

from jarvis.court.minister import Minister, MinisterProfile
from jarvis.core.task_router import (
    classify_task_type,
    route_to_minister,
    plan_dispatch,
    DispatchPlan,
)


def _mk_minister(title, domain, strengths, weaknesses=None, quality=0.8):
    profile = MinisterProfile(
        title=title,
        archetype="test",
        domain=domain,
        strengths=strengths,
        weaknesses=weaknesses or [],
        quality_score=quality,
    )
    return Minister(profile)


# 固定一组大臣用于路由测试
CODE_MIN = _mk_minister("工部尚书", "code", ["代码", "编程", "实现", "函数", "排序", "debug"], quality=0.9)
MATH_MIN = _mk_minister("太卜", "math", ["数学", "计算", "方程"], quality=0.85)
WRITE_MIN = _mk_minister("丞相", "general", ["写作", "总结", "计划"], quality=0.88)
ALL = [CODE_MIN, MATH_MIN, WRITE_MIN]


class TestClassifyTaskType:
    def test_code(self):
        assert classify_task_type("写一个快速排序函数") == "code"

    def test_math(self):
        assert classify_task_type("计算 1+1 等于多少") == "math"

    def test_writing(self):
        assert classify_task_type("帮我写一篇总结文章") == "writing"

    def test_research(self):
        assert classify_task_type("搜索最新论文并调研") == "research"

    def test_security(self):
        assert classify_task_type("审计这段代码的安全漏洞") == "security"

    def test_planning(self):
        assert classify_task_type("制定本周的工作计划") == "planning"

    def test_general_fallback(self):
        assert classify_task_type("你好") == "general"


class TestRouteToMinister:
    def test_routes_code_to_code_minister(self):
        m, score = route_to_minister("写一个快速排序函数", ALL)
        assert m is CODE_MIN
        assert score > 0

    def test_routes_math_to_math_minister(self):
        m, _ = route_to_minister("计算积分方程", ALL)
        assert m is MATH_MIN

    def test_type_aware_boost(self):
        # 不带 type_aware 时仍应路由到 code（因 strengths 命中），但 type_aware 不应改变结果
        m_aware, _ = route_to_minister("实现代码功能", ALL, type_aware=True)
        m_plain, _ = route_to_minister("实现代码功能", ALL, type_aware=False)
        assert m_aware is CODE_MIN
        assert m_plain is CODE_MIN

    def test_tie_break_by_quality(self):
        # 两个同域同 strengths 的大臣，质量分高者胜
        a = _mk_minister("A", "code", ["代码"], quality=0.5)
        b = _mk_minister("B", "code", ["代码"], quality=0.95)
        chosen, _ = route_to_minister("写代码", [a, b])
        assert chosen is b

    def test_default_fallback_when_no_match(self):
        # 所有大臣都完全不匹配且提供 default
        a = _mk_minister("A", "math", ["数学"], quality=0.5)
        b = _mk_minister("B", "math", ["方程"], quality=0.5)
        default = _mk_minister("D", "general", ["兜底"], quality=0.6)
        chosen, score = route_to_minister("帮我写首诗", [a, b], default=default)
        assert chosen is default
        assert score == 0.0

    def test_accepts_edict(self):
        from jarvis.court.minister import Edict
        edict = Edict(edict_id="e1", intent="写一个 Python 函数")
        m, _ = route_to_minister(edict, ALL)
        assert m is CODE_MIN


class TestPlanDispatch:
    def test_returns_dispatch_plan(self):
        plan = plan_dispatch("写一个快速排序函数", ALL)
        assert isinstance(plan, DispatchPlan)
        assert plan.minister is CODE_MIN
        assert plan.task_type == "code"
        assert plan.suggested_tier in ("cheap", "standard", "premium")

    def test_plan_uses_default_when_needed(self):
        a = _mk_minister("A", "math", ["数学"], quality=0.5)
        default = _mk_minister("D", "general", ["兜底"], quality=0.6)
        plan = plan_dispatch("随便聊聊天", [a], default=default)
        assert plan.minister is default
        assert plan.task_type == "general"
