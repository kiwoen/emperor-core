"""Adaptive Prompt Template System.

Manages per-capability prompt templates with versioning, performance
tracking, and heuristic auto-optimization (zero API cost).

Usage:
    from jarvis.prompt_template import PromptTemplateManager

    mgr = PromptTemplateManager(data_dir="/path/to/data")
    prompt = mgr.build_prompt("math", "What is 17 * 23?")
    mgr.record_feedback("math", 0.85)
    mgr.auto_optimize("math")
"""

from __future__ import annotations

import copy
import json
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.prompt_template")

# ══════════════════════════════════════════════════════════════════
# Built-in default templates for 12 capabilities
# ══════════════════════════════════════════════════════════════════

DEFAULT_TEMPLATES: dict[str, dict] = {
    "datetime": {
        "version": 1,
        "system_prompt": "你是时间日期专家，准确回答当前时间和时区相关问题。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "weather": {
        "version": 1,
        "system_prompt": "你是天气预报助手，准确提供天气查询结果。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "math": {
        "version": 1,
        "system_prompt": "你是数学计算专家，安全准确地执行数学运算。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "science": {
        "version": 1,
        "system_prompt": "你是科学知识库，提供准确的自然科学知识。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "code": {
        "version": 1,
        "system_prompt": "你是编程助手，擅长 Python、JavaScript 等语言。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "translation": {
        "version": 1,
        "system_prompt": "你是多语言翻译专家，准确流畅地进行翻译。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "research": {
        "version": 1,
        "system_prompt": "你是信息检索与综合分析专家，善于调研和总结。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "language": {
        "version": 1,
        "system_prompt": "你是语言处理专家，精通文本分析和语言理解。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "medicine": {
        "version": 1,
        "system_prompt": "你是医学知识助手，提供可靠的医学参考信息。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "engineering": {
        "version": 1,
        "system_prompt": "你是工程技术顾问，提供专业工程建议。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "general": {
        "version": 1,
        "system_prompt": "你是通用 AI 助手，全面处理各类问题。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
    "news": {
        "version": 1,
        "system_prompt": "你是新闻聚合助手，快速提供最新资讯。",
        "prompt_prefix": "",
        "prompt_suffix": "",
        "examples": [],
        "performance_score": 0.8,
        "last_updated": "",
        "frozen": False,
    },
}

# ══════════════════════════════════════════════════════════════════
# Heuristic optimization phrases for auto_optimize
# ══════════════════════════════════════════════════════════════════

_OPTIMIZATION_PHRASES = [
    "请确保回答准确、简洁、条理清晰。",
    "回答时优先使用结构化格式（列表、表格）。",
    "如有不确定的信息，请明确标注。",
    "回答应直击要点，避免冗余。",
    "提供示例以增强可理解性。",
    "优先使用中文回答，除非用户指定其他语言。",
    "回答前先理解用户意图，确保切题。",
    "对复杂问题分步骤解答。",
]

_EXAMPLE_POOL: dict[str, list[dict]] = {
    "math": [
        {"input": "计算 25 * 4 + 10", "output": "25 × 4 + 10 = 100 + 10 = 110"},
        {"input": "求 100 的平方根", "output": "100 的平方根是 10"},
    ],
    "datetime": [
        {"input": "现在是几点？", "output": "当前时间是 2026-07-16 14:30:00 (星期四)"},
        {"input": "今天星期几？", "output": "今天是星期四"},
    ],
    "weather": [
        {"input": "北京天气怎么样？", "output": "北京当前晴，温度 28°C，湿度 45%，风力 3 级"},
    ],
    "code": [
        {"input": "用 Python 写一个斐波那契函数", "output": "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        yield a\n        a, b = b, a + b"},
    ],
    "general": [
        {"input": "介绍一下人工智能", "output": "人工智能（AI）是计算机科学的一个分支，旨在创建能够模拟人类智能的系统。"},
    ],
}


# ══════════════════════════════════════════════════════════════════
# PromptTemplateManager
# ══════════════════════════════════════════════════════════════════


class PromptTemplateManager:
    """Manages per-capability prompt templates with versioning and optimization.

    Templates are stored as JSON files in ``<data_dir>/templates/``,
    one file per capability: ``<data_dir>/templates/math.json``.

    A history of older versions is kept within the template file itself
    under a ``_history`` key to support rollback.
    """

    def __init__(self, data_dir: str = "") -> None:
        if data_dir:
            self._templates_dir = Path(data_dir) / "templates"
        else:
            self._templates_dir = Path.cwd() / "templates"
        self._templates_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}

    # ── I/O ────────────────────────────────────────────────────────

    def _template_path(self, capability: str) -> Path:
        return self._templates_dir / f"{capability}.json"

    def load(self, capability: str) -> dict:
        """Load a template from disk, falling back to built-in defaults."""
        if capability in self._cache:
            return self._cache[capability]

        file_path = self._template_path(capability)
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    template = json.load(f)
                self._cache[capability] = template
                return template
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "[PromptTemplate] Failed to load '%s': %s, using default",
                    capability, exc,
                )

        # Fall back to built-in default
        default = copy.deepcopy(DEFAULT_TEMPLATES.get(capability))
        if default is None:
            # Unknown capability — create a generic default
            default = {
                "version": 1,
                "system_prompt": f"你是 {capability} 领域的 AI 助手。",
                "prompt_prefix": "",
                "prompt_suffix": "",
                "examples": [],
                "performance_score": 0.7,
                "last_updated": "",
                "frozen": False,
            }
        self._cache[capability] = default
        return default

    def save(self, capability: str, template: dict) -> None:
        """Save a template to disk and update cache."""
        template["last_updated"] = datetime.now(timezone.utc).isoformat()
        file_path = self._template_path(capability)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2, ensure_ascii=False, default=str)
        self._cache[capability] = template
        logger.debug("[PromptTemplate] Saved template '%s' v%d", capability, template.get("version", 0))

    # ── Prompt building ────────────────────────────────────────────

    def build_prompt(
        self,
        capability: str,
        user_query: str,
        context: Optional[dict] = None,
    ) -> str:
        """Build a full prompt string from the template.

        Assembly order:
            system_prompt + prompt_prefix + context + user_query + prompt_suffix + examples
        """
        template = self.load(capability)

        parts: list[str] = []

        # System prompt
        system = template.get("system_prompt", "")
        if system:
            parts.append(system)

        # Prefix
        prefix = template.get("prompt_prefix", "")
        if prefix:
            parts.append(prefix)

        # Context (optional)
        if context:
            ctx_str = "\n".join(f"{k}: {v}" for k, v in context.items())
            if ctx_str:
                parts.append(f"上下文信息：\n{ctx_str}")

        # User query
        parts.append(user_query)

        # Suffix
        suffix = template.get("prompt_suffix", "")
        if suffix:
            parts.append(suffix)

        # Examples (few-shot)
        examples = template.get("examples", [])
        if examples:
            example_text = "示例：\n"
            for ex in examples:
                example_text += f"输入: {ex['input']}\n输出: {ex['output']}\n\n"
            parts.append(example_text.strip())

        return "\n\n".join(parts)

    # ── Feedback ───────────────────────────────────────────────────

    def record_feedback(self, capability: str, score: float) -> dict:
        """Record user feedback and update the running performance score.

        Uses exponential moving average: new_score = 0.7 * old + 0.3 * new
        """
        template = self.load(capability)
        old_score = template.get("performance_score", 0.7)
        # Exponential moving average
        new_score = round(0.7 * old_score + 0.3 * score, 4)
        template["performance_score"] = new_score
        self.save(capability, template)
        logger.info(
            "[PromptTemplate] Feedback for '%s': %.2f → %.2f (raw %.2f)",
            capability, old_score, new_score, score,
        )
        return template

    # ── Auto-optimize ──────────────────────────────────────────────

    def auto_optimize(self, capability: str) -> dict:
        """Heuristically optimize a template when performance is low.

        Only optimizes if:
        - performance_score < 0.6
        - template is not frozen
        - version < 10 (prevent unbounded growth)

        Optimization strategies (zero API cost):
        1. Append an optimization phrase to system_prompt
        2. Add a relevant example from the pool
        3. Slightly rephrase the system_prompt
        """
        template = self.load(capability)

        if template.get("frozen", False):
            logger.info("[PromptTemplate] '%s' is frozen, skipping auto_optimize", capability)
            return template

        if template.get("version", 1) >= 10:
            logger.info("[PromptTemplate] '%s' reached max version 10, skipping auto_optimize", capability)
            return template

        score = template.get("performance_score", 0.7)
        if score >= 0.6:
            logger.info(
                "[PromptTemplate] '%s' score %.2f >= 0.6, no optimization needed",
                capability, score,
            )
            return template

        # Save current version in history for rollback
        history = template.setdefault("_history", [])
        history.append({
            "version": template["version"],
            "system_prompt": template["system_prompt"],
            "prompt_prefix": template["prompt_prefix"],
            "prompt_suffix": template["prompt_suffix"],
            "examples": copy.deepcopy(template.get("examples", [])),
            "performance_score": template["performance_score"],
            "optimized_at": datetime.now(timezone.utc).isoformat(),
        })

        # Keep only last 5 history entries
        if len(history) > 5:
            history[:] = history[-5:]

        # Bump version
        old_version = template["version"]
        template["version"] = old_version + 1

        # Strategy 1: Append an optimization phrase
        phrase = random.choice(_OPTIMIZATION_PHRASES)
        current_system = template.get("system_prompt", "")
        if phrase not in current_system:
            template["system_prompt"] = current_system.rstrip("。. ") + "。" + phrase

        # Strategy 2: Add a relevant example if available
        pool = _EXAMPLE_POOL.get(capability, _EXAMPLE_POOL.get("general", []))
        if pool:
            existing_inputs = {ex.get("input", "") for ex in template.get("examples", [])}
            available = [ex for ex in pool if ex.get("input", "") not in existing_inputs]
            if available:
                chosen = random.choice(available)
                template.setdefault("examples", []).append(chosen)

        # Strategy 3: Reset performance score slightly upward (optimistic)
        # but keep it closer to the new baseline
        template["performance_score"] = round(max(template["performance_score"], 0.62), 4)

        self.save(capability, template)
        logger.info(
            "[PromptTemplate] Auto-optimized '%s': v%d → v%d, score reset to %.2f",
            capability, old_version, template["version"], template["performance_score"],
        )
        return template

    # ── List / Query ───────────────────────────────────────────────

    def list_templates(self) -> list[dict]:
        """List all templates with version, score, and frozen status."""
        results: list[dict] = []
        # Load from disk first
        if self._templates_dir.exists():
            for file_path in self._templates_dir.glob("*.json"):
                cap_name = file_path.stem
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        template = json.load(f)
                    results.append({
                        "capability": cap_name,
                        "version": template.get("version", 1),
                        "performance_score": template.get("performance_score", 0.7),
                        "frozen": template.get("frozen", False),
                        "system_prompt": template.get("system_prompt", ""),
                        "examples_count": len(template.get("examples", [])),
                        "last_updated": template.get("last_updated", ""),
                    })
                except (json.JSONDecodeError, OSError):
                    pass

        # Add built-in defaults that haven't been saved yet
        existing = {r["capability"] for r in results}
        for cap_name, default in DEFAULT_TEMPLATES.items():
            if cap_name not in existing:
                results.append({
                    "capability": cap_name,
                    "version": default.get("version", 1),
                    "performance_score": default.get("performance_score", 0.7),
                    "frozen": default.get("frozen", False),
                    "system_prompt": default.get("system_prompt", ""),
                    "examples_count": len(default.get("examples", [])),
                    "last_updated": "",
                })

        # Sort by score descending
        results.sort(key=lambda x: x["performance_score"], reverse=True)
        return results

    # ── Freeze / Unfreeze ──────────────────────────────────────────

    def freeze(self, capability: str) -> dict:
        """Freeze a template to prevent further auto-optimization."""
        template = self.load(capability)
        template["frozen"] = True
        self.save(capability, template)
        logger.info("[PromptTemplate] Frozen '%s'", capability)
        return template

    def unfreeze(self, capability: str) -> dict:
        """Unfreeze a template to allow auto-optimization."""
        template = self.load(capability)
        template["frozen"] = False
        self.save(capability, template)
        logger.info("[PromptTemplate] Unfrozen '%s'", capability)
        return template

    # ── Rollback ───────────────────────────────────────────────────

    def rollback(self, capability: str, version: int) -> dict:
        """Rollback a template to a specific historical version."""
        template = self.load(capability)
        history: list[dict] = template.get("_history", [])

        # Check if target version is current
        if version == template.get("version"):
            logger.info("[PromptTemplate] '%s' already at version %d, no rollback needed", capability, version)
            return template

        target = None
        for entry in history:
            if entry.get("version") == version:
                target = entry
                break

        if target is None:
            raise ValueError(
                f"Version {version} not found in history for capability '{capability}'. "
                f"Available: {[h.get('version') for h in history]}"
            )

        # Save current state to history before rollback
        history.append({
            "version": template["version"],
            "system_prompt": template["system_prompt"],
            "prompt_prefix": template["prompt_prefix"],
            "prompt_suffix": template["prompt_suffix"],
            "examples": copy.deepcopy(template.get("examples", [])),
            "performance_score": template["performance_score"],
            "rolled_back_at": datetime.now(timezone.utc).isoformat(),
        })

        # Restore target version
        template["version"] = version
        template["system_prompt"] = target["system_prompt"]
        template["prompt_prefix"] = target.get("prompt_prefix", "")
        template["prompt_suffix"] = target.get("prompt_suffix", "")
        template["examples"] = copy.deepcopy(target.get("examples", []))
        template["performance_score"] = target.get("performance_score", 0.7)

        if len(history) > 10:
            history[:] = history[-10:]

        self.save(capability, template)
        logger.info(
            "[PromptTemplate] Rolled back '%s' to v%d", capability, version,
        )
        return template

    # ── Get full detail ────────────────────────────────────────────

    def get_detail(self, capability: str) -> Optional[dict]:
        """Return the full template detail including history."""
        try:
            template = self.load(capability)
            result = copy.deepcopy(template)
            # Don't expose raw history in detail by default
            result["_history_count"] = len(result.get("_history", []))
            result.pop("_history", None)
            return result
        except Exception:
            return None
