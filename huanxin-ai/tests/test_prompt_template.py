"""Focused branch tests for prompt template persistence and optimization."""

from __future__ import annotations

import json

import pytest

from huanxin.prompt_template import PromptTemplateManager


def test_load_unknown_and_build_prompt_with_context(tmp_path):
    manager = PromptTemplateManager(str(tmp_path))
    prompt = manager.build_prompt(
        "unknown-capability",
        "answer this",
        context={"user": "alice", "mode": "safe"},
    )
    assert "unknown-capability" in prompt
    assert "user: alice" in prompt
    assert "mode: safe" in prompt
    assert "answer this" in prompt


def test_corrupt_file_falls_back_to_default(tmp_path):
    path = tmp_path / "templates"
    path.mkdir()
    (path / "math.json").write_text("{bad json", encoding="utf-8")
    manager = PromptTemplateManager(str(tmp_path))
    template = manager.load("math")
    assert template["version"] == 1
    assert template["system_prompt"]


def test_feedback_updates_score_and_persists(tmp_path):
    manager = PromptTemplateManager(str(tmp_path))
    updated = manager.record_feedback("math", 0.0)
    assert updated["performance_score"] == pytest.approx(0.56)
    reloaded = PromptTemplateManager(str(tmp_path)).load("math")
    assert reloaded["performance_score"] == pytest.approx(0.56)
    assert reloaded["last_updated"]


def test_auto_optimize_respects_guard_conditions(tmp_path):
    manager = PromptTemplateManager(str(tmp_path))

    frozen = manager.load("code")
    frozen["performance_score"] = 0.1
    frozen["frozen"] = True
    manager.save("code", frozen)
    assert manager.auto_optimize("code")["version"] == 1

    capped = manager.load("math")
    capped["performance_score"] = 0.1
    capped["version"] = 10
    manager.save("math", capped)
    assert manager.auto_optimize("math")["version"] == 10

    healthy = manager.load("weather")
    healthy["performance_score"] = 0.6
    manager.save("weather", healthy)
    assert manager.auto_optimize("weather")["version"] == 1


def test_auto_optimize_records_history_and_example(tmp_path):
    manager = PromptTemplateManager(str(tmp_path))
    template = manager.load("math")
    template["performance_score"] = 0.2
    manager.save("math", template)

    optimized = manager.auto_optimize("math")
    assert optimized["version"] == 2
    assert optimized["performance_score"] >= 0.62
    assert len(optimized["_history"]) == 1
    assert optimized["examples"]
    assert optimized["examples"][0]["input"]


def test_rollback_restores_previous_version(tmp_path):
    manager = PromptTemplateManager(str(tmp_path))
    original = manager.load("math")
    original_prompt = original["system_prompt"]
    original["performance_score"] = 0.2
    manager.save("math", original)
    manager.auto_optimize("math")

    restored = manager.rollback("math", 1)
    assert restored["version"] == 1
    assert restored["system_prompt"] == original_prompt

    with pytest.raises(ValueError, match="not found"):
        manager.rollback("math", 99)


def test_list_and_detail_hide_history(tmp_path):
    manager = PromptTemplateManager(str(tmp_path))
    template = manager.load("math")
    template["_history"] = [{"version": 0}]
    manager.save("math", template)

    detail = manager.get_detail("math")
    assert detail is not None
    assert "_history" not in detail
    assert detail["_history_count"] == 1

    listed = manager.list_templates()
    math = next(item for item in listed if item["capability"] == "math")
    assert math["examples_count"] == 0
    assert math["version"] == 1
