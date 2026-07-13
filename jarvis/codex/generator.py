"""
Codex Generator — code generation and refactoring engine.

Handles:
- Template-based code generation (skeleton, boilerplate)
- Pattern-based refactoring (rename, extract, format)
- Placeholder for LLM-based generation (wired via LLM layer)
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("jarvis.codex.generator")


class Generator:
    """Code generation and refactoring engine.

    Currently template/rule-based. LLM integration slot via the
    existing jarvis.core.llm layer.
    """

    TEMPLATES: dict[str, str] = {
        "python_module": (
            '"""\n{module_name} — {description}\n"""\n\n'
            "import logging\n\n"
            "logger = logging.getLogger(__name__)\n\n\n"
            "def main() -> None:\n    pass\n\n\n"
            'if __name__ == "__main__":\n    main()\n'
        ),
        "python_class": (
            "class {class_name}:\n"
            '    """{description}"""\n\n'
            "    def __init__(self) -> None:\n        pass\n"
        ),
        "python_test": (
            '"""Tests for {module_name}."""\n'
            "import pytest\n"
            "from {module_name} import {class_name}\n\n\n"
            "class Test{class_name}:\n"
            "    def test_init(self) -> None:\n"
            "        obj = {class_name}()\n"
            "        assert obj is not None\n"
        ),
        "python_fastapi": (
            "from fastapi import FastAPI\n\n"
            "app = FastAPI(title=\"{title}\")\n\n\n"
            '@app.get("/")\n'
            "async def root():\n"
            '    return {{"status": "ok"}}\n'
        ),
        "python_cli": (
            '"""\n{description}\n"""\n'
            "import argparse\n"
            "import sys\n\n\n"
            "def main() -> None:\n"
            "    parser = argparse.ArgumentParser(description=\"{description}\")\n"
            "    parser.add_argument(\"--verbose\", action=\"store_true\")\n"
            "    args = parser.parse_args()\n"
            "    print(f\"Running {description}...\")\n\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
    }

    def generate(self, payload: Any) -> dict[str, Any]:
        """Generate code from a template specification.

        Payload shape:
            {"template": str, "params": dict, "language": str}
        """
        if not isinstance(payload, dict):
            return {"error": "Payload must be a dict with 'template' and 'params' keys"}

        template_name = payload.get("template", "")
        params = payload.get("params", {})
        language = payload.get("language", "python")

        if template_name in self.TEMPLATES:
            try:
                code = self.TEMPLATES[template_name].format(**params)
                return {"code": code, "template": template_name, "language": language}
            except KeyError as e:
                return {"error": f"Missing parameter: {e}", "hint": f"Required params for '{template_name}'"}
        else:
            available = list(self.TEMPLATES.keys())
            return {
                "error": f"Unknown template: '{template_name}'",
                "available_templates": available,
            }

    def refactor(self, code: str, pattern: str) -> dict[str, Any]:
        """Apply a refactoring pattern to code.

        Supported patterns:
        - 'cleanup': remove trailing whitespace, normalize blank lines
        - 'rename': rename a symbol (requires extra params)
        - 'extract': extract a block to a function (placeholder)
        """
        if pattern == "cleanup":
            return self._refactor_cleanup(code)
        elif pattern == "rename":
            return self._refactor_rename(code, {})
        elif pattern == "extract":
            return self._refactor_extract(code, {})
        else:
            return {"error": f"Unknown refactoring pattern: '{pattern}'"}

    def _refactor_cleanup(self, code: str) -> dict[str, Any]:
        """Clean up code: strip trailing whitespace, normalize blank lines."""
        lines = code.splitlines()

        # Remove trailing whitespace
        lines = [l.rstrip() for l in lines]

        # Collapse 3+ consecutive blank lines into 2
        cleaned = []
        blank_count = 0
        for line in lines:
            if line.strip() == "":
                blank_count += 1
                if blank_count <= 2:
                    cleaned.append(line)
            else:
                blank_count = 0
                cleaned.append(line)

        new_code = "\n".join(cleaned)
        changed = new_code != code
        return {
            "pattern": "cleanup",
            "code": new_code,
            "changed": changed,
            "changes": ["trailing whitespace removed", "excess blank lines collapsed"] if changed else [],
        }

    def _refactor_rename(
        self, code: str, params: dict[str, str]
    ) -> dict[str, Any]:
        """Rename a symbol in code (naive string replacement — placeholder)."""
        old = params.get("old", "")
        new = params.get("new", "")
        if not old or not new:
            return {"error": "Rename requires 'old' and 'new' params"}

        # Word-boundary-aware replacement
        new_code = re.sub(rf"\b{re.escape(old)}\b", new, code)
        occurrences = len(re.findall(rf"\b{re.escape(old)}\b", code))
        return {
            "pattern": "rename",
            "code": new_code,
            "changed": occurrences > 0,
            "occurrences": occurrences,
        }

    def _refactor_extract(
        self, code: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract method placeholder — returns code as-is with note."""
        return {
            "pattern": "extract",
            "code": code,
            "changed": False,
            "note": "Automatic method extraction requires LLM integration (placeholder)",
        }
