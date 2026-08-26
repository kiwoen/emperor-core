"""genome_diff 的单元测试：把「系统对自己基因的改动」变成可审查的真实 diff。"""

from __future__ import annotations

import json

from huanxin.court.genome_diff import (
    GENOME_STATE_RELPATH,
    genome_state_diff,
    genome_state_file_content,
)


def _payload(temperature: float) -> dict:
    return {
        "version": 1,
        "metadata": {"cycle": 1, "total_genomes": 1},
        "genomes": [
            {"name": "m1", "domain": "math", "temperature": temperature,
             "confidence_baseline": 0.9, "exploration_rate": 0.3,
             "conservatism": 0.5, "prompt_mutation_rate": 0.1,
             "specialization_weight": 1.0, "generation": 0, "parent": ""},
        ],
    }


def test_identical_payloads_yield_empty_diff():
    before = _payload(0.7)
    after = json.loads(json.dumps(before))
    assert genome_state_diff(before, after) == ""


def test_changed_payload_yields_unified_diff():
    before = _payload(0.9)
    after = _payload(0.62)
    diff = genome_state_diff(before, after)
    assert diff, "改动后必须产出非空 diff"
    assert "temperature" in diff
    assert "0.9" in diff and "0.62" in diff
    # unified diff 头部
    assert "--- " in diff and "+++ " in diff
    assert f"b/{GENOME_STATE_RELPATH}" in diff


def test_new_file_diff_uses_dev_null():
    before: dict = {}
    after = _payload(0.5)
    diff = genome_state_diff(before, after)
    assert "/dev/null" in diff, "全新文件 diff 必须以 /dev/null 为来源"
    assert f"b/{GENOME_STATE_RELPATH}" in diff


def test_file_content_has_trailing_newline():
    content = genome_state_file_content(_payload(0.4))
    assert content.endswith("\n")
    # 可反序列化
    assert json.loads(content)["genomes"][0]["name"] == "m1"
