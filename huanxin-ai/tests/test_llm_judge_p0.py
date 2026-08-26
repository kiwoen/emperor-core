"""
P0.6 对 huanxin.llm_judge 的回归 / 行为测试。

覆盖主理人要求：
1. LLMJudge.evaluate 在**启发式模式**下发出 warning（UserWarning + logger.warning）。
2. accuracy 维度在启发式模式的 reasoning 含 "heuristic" 字样（证明不再冒充事实正确）。
3. 默认 HUANXIN_JUDGE_MODE=deterministic 时，llm_judge **不再**以关键词重叠作为
   权威 accuracy（行为证明：确定性 accuracy 不走关键词重叠）。
4. llm 模式无 key 时，evaluate 显式回退启发式并告警（绝不静默假高分）。
"""

import logging
import pathlib
import re
import warnings

import pytest

from huanxin.llm_judge import LLMJudge, JudgingCriteria

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LLM_JUDGE_SRC = REPO_ROOT / "huanxin" / "llm_judge.py"


def _accuracy(res):
    return next(d for d in res.breakdown if d.criterion == JudgingCriteria.ACCURACY)


# ══════════════════════════════════════════════════════════════════
# 1 + 2. 启发式模式：warning + accuracy reasoning 含 "heuristic"
# ══════════════════════════════════════════════════════════════════


def test_heuristic_mode_emits_warning_and_marks_heuristic(monkeypatch, caplog):
    monkeypatch.setenv("HUANXIN_JUDGE_MODE", "heuristic")
    judge = LLMJudge()
    with caplog.at_level(logging.WARNING):
        with pytest.warns(UserWarning):
            res = judge.evaluate(
                "法国的首都是巴黎。", "法国的首都是巴黎。",
                criteria=[JudgingCriteria.ACCURACY],
            )
    # logger.warning 被触发
    assert any(
        ("非权威" in r.message) or ("heuristic" in r.message.lower())
        for r in caplog.records
    )
    # accuracy reasoning 明确标注 heuristic，证明不再冒充事实正确
    acc = _accuracy(res)
    assert "heuristic" in acc.reasoning.lower()


# ══════════════════════════════════════════════════════════════════
# 3. 默认确定性模式不再用关键词重叠作为权威 accuracy
# ══════════════════════════════════════════════════════════════════


def test_default_mode_is_deterministic_not_keyword_overlap(monkeypatch):
    monkeypatch.delenv("HUANXIN_JUDGE_MODE", raising=False)
    judge = LLMJudge()
    # 关键词高度重叠但事实错误的内容
    output = "法国 巴黎 巴黎 法国 首都 首都 法国 巴黎"
    expected = "法国 巴黎 巴黎 法国 德国 首都 法国 巴黎"
    res = judge.evaluate(output, expected, criteria=[JudgingCriteria.ACCURACY])
    acc = _accuracy(res)
    # 确定性模式走归一化匹配（非关键词重叠），事实错误 => 0.0
    assert "deterministic" in acc.reasoning.lower()
    assert acc.score == 0.0


def test_keyword_overlap_only_in_heuristic_mode(monkeypatch):
    judge = LLMJudge()
    output = "法国 巴黎 巴黎 法国 首都 首都 法国 巴黎"
    expected = "法国 巴黎 巴黎 法国 德国 首都 法国 巴黎"

    monkeypatch.setenv("HUANXIN_JUDGE_MODE", "heuristic")
    h_score = judge.evaluate(
        output, expected, criteria=[JudgingCriteria.ACCURACY]
    ).breakdown[0].score

    monkeypatch.setenv("HUANXIN_JUDGE_MODE", "deterministic")
    d_score = judge.evaluate(
        output, expected, criteria=[JudgingCriteria.ACCURACY]
    ).breakdown[0].score

    # 启发式用关键词重叠（高），确定性不用（事实错误 => 0）。证明确定性路径不依赖重叠。
    assert h_score > d_score
    assert d_score == 0.0


def test_source_accuracy_not_using_keyword_overlap_in_deterministic_path():
    """源码层面确认：_evaluate_accuracy 在确定性分支不调用 _keyword_overlap_score。

    注意：启发式分支（`mode == "heuristic"`）合法使用关键词重叠，但会被明确标注
    为 [heuristic] 非权威；此处只检查确定性分支。
    """
    text = LLM_JUDGE_SRC.read_text(encoding="utf-8")
    # 找到 `def _evaluate_accuracy(` 到同层下一个 `def ` 之前的函数体
    m = re.search(r"def _evaluate_accuracy\(.*?\n(?=\ndef )", text, re.S)
    assert m, "未能定位 _evaluate_accuracy 函数"
    body = m.group(0)
    # 确定性分支：包含 "[deterministic]" 且调用 DeterministicJudge，而不引用 _keyword_overlap_score
    assert "[deterministic]" in body
    assert "DeterministicJudge" in body
    # 整个函数的关键词重叠使用只能出现在 heuristic 分支内（含 "mode == \"heuristic\""），
    # 我们用一个粗略但稳健的检查：确定性返回分支中不得出现 _keyword_overlap_score。
    det_branch = body.split('if mode == "heuristic":')[0]
    assert "_keyword_overlap_score" not in det_branch


# ══════════════════════════════════════════════════════════════════
# 4. llm 模式无 key -> 显式回退启发式并告警
# ══════════════════════════════════════════════════════════════════


def test_llm_mode_falls_back_to_heuristic_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("HUANXIN_JUDGE_MODE", "llm")
    monkeypatch.delenv("HUANXIN_LLM_API_KEY", raising=False)
    judge = LLMJudge()
    with caplog.at_level(logging.WARNING):
        with pytest.warns(UserWarning):
            res = judge.evaluate(
                "法国的首都是巴黎。", "法国的首都是巴黎。",
                criteria=[JudgingCriteria.ACCURACY],
            )
    assert any(
        ("LLM 裁判不可用" in r.message) or ("非权威" in r.message)
        for r in caplog.records
    )
    acc = _accuracy(res)
    assert ("heuristic" in acc.reasoning.lower()) or ("degraded" in acc.reasoning.lower())
