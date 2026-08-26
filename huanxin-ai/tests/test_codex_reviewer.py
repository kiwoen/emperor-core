"""Tests for the multi-dimensional CodeReviewer (八维加权代码审查)."""
import pytest

from huanxin.codex.reviewer import (
    CodeReviewer,
    Dimension,
    Severity,
    detect_language,
    detect_code_type,
)
from huanxin.hermes.bus import MessageBus, Topic
from huanxin.codex.engine import CodexEngine
from huanxin.codex.analyzer import Analyzer
from huanxin.codex.generator import Generator


# ============================================================================
# Fixtures / sample code
# ============================================================================

SECRET_CODE = '''
import os
password = "hardcoded123"
def process(data, config=[], verbose=False):
    result = []
    for x in data:
        result.append(x * 2)
    print("done")
    try:
        return result
    except:
        pass
'''

CLEAN_CODE = '''
"""A clean module."""

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
'''

WEB_CODE = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/items")
def get_items():
    return db.query("SELECT * FROM items WHERE id = " + request.args.get("id"))
'''

BAD_PARENS = '''
def foo() {
    return (
}
'''


# ============================================================================
# Language / type detection
# ============================================================================

class TestDetection:
    def test_detect_language_python(self):
        assert detect_language("def foo():\n    return 1\n") == "python"

    def test_detect_language_hint(self):
        assert detect_language("x = 1", hint=".py") == "python"
        assert detect_language("x = 1", hint="python") == "python"

    def test_detect_language_javascript(self):
        assert detect_language("function f() { return 1; }") == "javascript"

    def test_detect_language_typescript(self):
        assert detect_language("let x: string = 'a';") == "typescript"

    def test_detect_language_go(self):
        assert detect_language("package main\nfunc main() {}") == "go"

    def test_detect_language_generic_fallback(self):
        assert detect_language("some unknown DSL tokens") == "generic"

    def test_detect_code_type_web_service(self):
        assert detect_code_type(WEB_CODE, "python") == "web_service"

    def test_detect_code_type_library(self):
        assert detect_code_type(CLEAN_CODE, "python") in ("library", "script")

    def test_detect_code_type_test_suite(self):
        assert detect_code_type("def test_foo():\n    assert 1 == 1\n", "python") == "test_suite"


# ============================================================================
# Multi-dimensional review
# ============================================================================

class TestReview:
    def test_review_returns_report(self):
        r = CodeReviewer().review(SECRET_CODE, language="python")
        assert r.language == "python"
        assert 0 <= r.overall_score <= 10
        assert r.grade in ("S", "A", "B", "C", "D")

    def test_review_finds_hardcoded_secret(self):
        r = CodeReviewer().review(SECRET_CODE, language="python")
        sec = next(d for d in r.dimensions if d.dimension == Dimension.SECURITY)
        assert any("hardcoded" in i.message.lower() or i.rule_id == "sec.hardcoded_secret"
                   for i in sec.issues)
        assert any(i.severity == Severity.CRITICAL for i in r.issues)

    def test_review_finds_mutable_default(self):
        r = CodeReviewer().review(SECRET_CODE, language="python")
        corr = next(d for d in r.dimensions if d.dimension == Dimension.CORRECTNESS)
        assert any(i.rule_id == "py.mutable_default" for i in corr.issues)

    def test_review_finds_bare_except(self):
        r = CodeReviewer().review(SECRET_CODE, language="python")
        obs = next(d for d in r.dimensions if d.dimension == Dimension.OBSERVABILITY)
        assert any(i.rule_id == "py.bare_except" for i in obs.issues)

    def test_review_honest_na_for_non_concurrent_non_test(self):
        r = CodeReviewer().review(SECRET_CODE, language="python")
        assert "testing" in r.honest_na
        assert "concurrency" in r.honest_na
        # 维度对象中 applicability 标记正确
        testing_dim = next(d for d in r.dimensions if d.dimension == Dimension.TESTING)
        assert testing_dim.applicable is False
        assert testing_dim.score is None

    def test_review_clean_code_high_score(self):
        r = CodeReviewer().review(CLEAN_CODE, language="python")
        assert r.overall_score >= 8.0
        assert r.grade in ("S", "A")

    def test_review_prioritized_issues_order(self):
        r = CodeReviewer().review(SECRET_CODE, language="python")
        sevs = [i.severity for i in r.prioritized_issues]
        # CRITICAL 应排在最前
        assert Severity.CRITICAL in sevs
        assert sevs[0] == Severity.CRITICAL

    def test_review_web_service_type_specific(self):
        r = CodeReviewer().review(WEB_CODE, language="python")
        assert r.code_type == "web_service"
        # 未做输入校验 → 应触发类型专项问题
        assert r.type_specific.get("input_validation") == "missing"
        sec = next(d for d in r.dimensions if d.dimension == Dimension.SECURITY)
        assert any(i.rule_id == "type.ws.no_validation" for i in sec.issues)

    def test_review_non_python_paren_mismatch(self):
        r = CodeReviewer().review(BAD_PARENS, language="python")
        corr = next(d for d in r.dimensions if d.dimension == Dimension.CORRECTNESS)
        # 括号不匹配（即使 AST 失败，也应被 generic 规则捕获或 syntax 规则捕获）
        assert len(corr.issues) >= 1

    def test_to_markdown_sections(self):
        r = CodeReviewer().review(SECRET_CODE, language="python")
        md = CodeReviewer.to_markdown(r)
        assert "## 代码多维审查报告" in md
        assert "八维评分" in md
        assert "问题清单" in md
        assert "优先修改清单" in md
        assert "综合结论" in md
        assert "🔴" in md


# ============================================================================
# Integration: codex.review.multidim via Hermes bus
# ============================================================================

class TestCodexMultidimBus:
    @pytest.mark.asyncio
    async def test_review_multidim_via_bus(self):
        bus = MessageBus()
        reviewer = CodeReviewer()
        engine = CodexEngine(bus, Analyzer(), Generator(), reviewer=reviewer)
        await engine.start()

        reply = await bus.request(
            Topic("codex.review.multidim.python"),
            payload={"code": SECRET_CODE, "language": "python"},
            sender="test",
            timeout=5.0,
        )
        payload = reply.payload
        assert payload["language"] == "python"
        assert payload["grade"] in ("S", "A", "B", "C", "D")
        assert any(i["rule_id"] == "sec.hardcoded_secret" for i in payload["issues"])
        assert "markdown" in payload and "## 代码多维审查报告" in payload["markdown"]
        await engine.shutdown()
