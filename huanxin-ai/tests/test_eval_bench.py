"""
P0.6 可信评测基准测试（零网络、沙箱可跑）。

覆盖：
* DeterministicJudge 对"正确输出"判 passed=True、对"明显错误输出"判 passed=False。
* run_suite 返回的 pass_rate 在预期区间；per_domain 正确聚合。
* LLMBackedJudge 在无 API key 时**不静默**返回高分——明确 raise + logger.warning。
* get_judge 工厂的确定性 / llm 路径行为。
"""

import logging
import pathlib

import pytest

from huanxin.eval_bench import (
    DeterministicJudge,
    JudgeUnavailableError,
    LLMBackedJudge,
    build_canonical_suite,
    get_judge,
    run_suite,
)
from huanxin.eval_bench.criteria import EvalCase
from huanxin.eval_bench.judges import default_correctness

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LLM_JUDGE_SRC = REPO_ROOT / "huanxin" / "llm_judge.py"


# ══════════════════════════════════════════════════════════════════
# DeterministicJudge 行为
# ══════════════════════════════════════════════════════════════════


def test_deterministic_judge_passes_reference_outputs():
    suite = build_canonical_suite()
    judge = DeterministicJudge()
    for case in suite.cases:
        out = suite.reference_output_for(case)
        res = judge.judge(case, out)
        assert res.passed, f"case {case.id} 应判通过（黄金参考输出）; reason={res.reason}"
        assert res.score == 1.0
        # structure 只是诊断，不影响 pass/fail
        assert "structure_ok" in res.diagnostics


def test_deterministic_judge_fails_wrong_output():
    suite = build_canonical_suite()
    judge = DeterministicJudge()
    wrong = "完全无关的错误答案 xxxxx 999"
    for case in suite.cases:
        res = judge.judge(case, wrong)
        assert not res.passed, f"case {case.id} 对明显错误输出应判不通过"
        assert res.score == 0.0


def test_deterministic_judge_requires_factual_correctness_not_overlap():
    # 关键词高度重叠但事实错误 -> 必须判不通过（证明不用关键词重叠冒充正确性）
    case = EvalCase(
        input="法国的首都是？",
        expected="巴黎",
        gold_validator=default_correctness,
        domain="factual",
        id="t.overlap",
    )
    judge = DeterministicJudge()
    wrong_but_overlapping = "法国 巴黎 巴黎 法国 德国 首都 法国 巴黎"
    res = judge.judge(case, wrong_but_overlapping)
    assert not res.passed
    assert res.score == 0.0


# ══════════════════════════════════════════════════════════════════
# run_suite 聚合
# ══════════════════════════════════════════════════════════════════


def test_run_suite_pass_rate_interval():
    suite = build_canonical_suite()
    report = run_suite(suite)
    assert report.cases == len(suite.cases)
    assert report.passed == report.cases
    assert report.failed == 0
    assert 0.99 <= report.pass_rate <= 1.0
    # 每个 domain 都应满分
    assert all(abs(v - 1.0) < 1e-9 for v in report.per_domain.values())
    # 可序列化为 dict
    d = report.to_dict()
    assert "pass_rate" in d and "per_domain" in d
    assert d["pass_rate"] == 1.0


def test_run_suite_wrong_output_lowers_rate():
    suite = build_canonical_suite()
    outputs = dict(suite.reference_outputs)
    first_id = suite.cases[0].id
    outputs[first_id] = "完全无关的错误答案 xxxxx 999"
    report = run_suite(suite, outputs)
    assert report.pass_rate < 1.0
    assert report.failed >= 1
    assert report.passed == report.cases - 1


# ══════════════════════════════════════════════════════════════════
# LLMBackedJudge：无 key 时绝不静默假高分
# ══════════════════════════════════════════════════════════════════


def test_llmbacked_judge_no_key_raises_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        with pytest.raises(JudgeUnavailableError):
            LLMBackedJudge()
    assert any("HUANXIN_LLM_API_KEY" in r.message for r in caplog.records)


def test_get_judge_llm_no_key_raises():
    with pytest.raises(JudgeUnavailableError):
        get_judge("llm")


def test_get_judge_deterministic_returns_deterministic():
    j = get_judge("deterministic")
    assert isinstance(j, DeterministicJudge)


def test_llmbacked_does_not_silent_high_score():
    """无 key 时 get_judge('llm') 必须报错，而不能返回一个假高分的裁判。"""
    with pytest.raises(JudgeUnavailableError):
        get_judge("llm")
