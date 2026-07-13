"""
Codex Analyzer — static code analysis engine.

Performs AST-based analysis on source code:
- Complexity metrics (cyclomatic, cognitive)
- Code smells detection
- Import analysis
- Structure extraction (functions, classes, methods)
- Diff review with rule-based checks
"""

from __future__ import annotations

import ast
import logging
from typing import Any

logger = logging.getLogger("jarvis.codex.analyzer")


class Analyzer:
    """Static code analyzer using Python's AST module.

    Extensible: add language-specific analyzers via strategy pattern.
    """

    SUPPORTED_LANGUAGES = {"python", "javascript", "typescript", "go", "rust"}

    def analyze(self, payload: Any) -> dict[str, Any]:
        """Analyze source code and return structured report.

        Payload shape: {"code": str, "language": str, ...}
        """
        if not isinstance(payload, dict):
            return {"error": "Payload must be a dict with 'code' and 'language' keys"}

        code = payload.get("code", "")
        language = payload.get("language", "python").lower()

        if language == "python":
            return self._analyze_python(code)
        else:
            return self._analyze_generic(code, language)

    # ------------------------------------------------------------------
    # Python analysis
    # ------------------------------------------------------------------

    def _analyze_python(self, code: str) -> dict[str, Any]:
        """AST-based analysis for Python code."""
        result: dict[str, Any] = {
            "language": "python",
            "lines": len(code.splitlines()),
            "chars": len(code),
            "functions": [],
            "classes": [],
            "imports": [],
            "issues": [],
            "complexity": {},
        }

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            result["issues"].append({
                "type": "syntax_error",
                "message": str(e),
                "severity": "error",
            })
            return result

        for node in ast.walk(tree):
            # Functions
            if isinstance(node, ast.FunctionDef):
                func_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "args": [a.arg for a in node.args.args],
                    "decorators": [ast.unparse(d) for d in node.decorator_list],
                }
                result["functions"].append(func_info)

            # Classes
            elif isinstance(node, ast.ClassDef):
                class_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "methods": [n.name for n in node.body if isinstance(n, ast.FunctionDef)],
                    "bases": [ast.unparse(b) for b in node.bases],
                }
                result["classes"].append(class_info)

            # Imports
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    result["imports"].append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    result["imports"].append(f"{module}.{alias.name}" if module else alias.name)

        # Complexity estimation (simple cyclomatic)
        complexity = self._estimate_complexity(tree)
        result["complexity"] = complexity

        # Code smell detection
        smells = self._detect_smells(code, tree, result)
        result["issues"].extend(smells)

        return result

    def _estimate_complexity(self, tree: ast.AST) -> dict[str, Any]:
        """Estimate cyclomatic complexity."""
        branches = 0
        functions_complexity: dict[str, int] = {}

        class ComplexityVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_func = None
                self.counts: dict[str, int] = {}

            def visit_FunctionDef(self, node):
                prev = self.current_func
                self.current_func = node.name
                self.counts[node.name] = 1  # base
                self.generic_visit(node)
                self.current_func = prev

            def visit_If(self, node):
                if self.current_func:
                    self.counts[self.current_func] += 1
                self.generic_visit(node)

            def visit_For(self, node):
                if self.current_func:
                    self.counts[self.current_func] += 1
                self.generic_visit(node)

            def visit_While(self, node):
                if self.current_func:
                    self.counts[self.current_func] += 1
                self.generic_visit(node)

            def visit_ExceptHandler(self, node):
                if self.current_func:
                    self.counts[self.current_func] += 1
                self.generic_visit(node)

            def visit_BoolOp(self, node):
                if self.current_func and isinstance(node.op, (ast.And, ast.Or)):
                    self.counts[self.current_func] += len(node.values) - 1
                self.generic_visit(node)

        visitor = ComplexityVisitor()
        visitor.visit(tree)

        total = sum(visitor.counts.values())
        return {
            "total_cyclomatic": total,
            "per_function": visitor.counts,
            "rating": "low" if total < 10 else "moderate" if total < 30 else "high",
        }

    def _detect_smells(self, code: str, tree: ast.AST, result: dict) -> list[dict]:
        """Detect common code smells."""
        issues: list[dict] = []
        lines = code.splitlines()

        # Long functions (> 50 lines)
        for func in result.get("functions", []):
            # Approximate end line
            func_lines = len(lines)  # default
            try:
                end_line = self._find_func_end(func["line"], lines)
                func_lines = end_line - func["line"] + 1
            except Exception:
                pass
            if func_lines > 50:
                issues.append({
                    "type": "long_function",
                    "message": f"Function '{func['name']}' is {func_lines} lines long (threshold: 50)",
                    "severity": "warning",
                    "line": func["line"],
                })

        # Too many args (> 5)
        for func in result.get("functions", []):
            if len(func["args"]) > 5:
                issues.append({
                    "type": "too_many_args",
                    "message": f"Function '{func['name']}' has {len(func['args'])} arguments",
                    "severity": "info",
                    "line": func["line"],
                })

        # Nested function depth
        class DepthVisitor(ast.NodeVisitor):
            def __init__(self):
                self.max_depth = 0
                self.current = 0

            def visit_FunctionDef(self, node):
                self.current += 1
                self.max_depth = max(self.max_depth, self.current)
                self.generic_visit(node)
                self.current -= 1

        dv = DepthVisitor()
        dv.visit(tree)
        if dv.max_depth > 2:
            issues.append({
                "type": "nested_functions",
                "message": f"Nested function depth is {dv.max_depth}",
                "severity": "info",
            })

        return issues

    @staticmethod
    def _find_func_end(start_line: int, lines: list[str]) -> int:
        """Approximate function end by dedent."""
        if start_line >= len(lines):
            return start_line
        base = lines[start_line - 1]
        indent = len(base) - len(base.lstrip())
        # Walk forward until line with same or less indent (and not empty)
        for i in range(start_line, len(lines)):
            line = lines[i]
            if not line.strip():
                continue
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= indent:
                return i  # line number (1-based)
        return len(lines)

    # ------------------------------------------------------------------
    # Generic / placeholder analysis
    # ------------------------------------------------------------------

    def _analyze_generic(self, code: str, language: str) -> dict[str, Any]:
        """Fallback analysis for non-Python languages (line-based metrics)."""
        lines = code.splitlines()
        return {
            "language": language,
            "lines": len(lines),
            "chars": len(code),
            "functions": [],
            "classes": [],
            "imports": [],
            "issues": [],
            "complexity": {"note": f"Full AST analysis not available for {language}"},
        }

    # ------------------------------------------------------------------
    # Diff review
    # ------------------------------------------------------------------

    def review_diff(self, diff: str) -> dict[str, Any]:
        """Review a unified diff for common issues."""
        lines = diff.splitlines()
        added_lines = [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")]
        removed_lines = [l[1:] for l in lines if l.startswith("-") and not l.startswith("---")]

        issues: list[dict] = []
        suggestions: list[str] = []

        # Check for debug prints
        for i, line in enumerate(added_lines):
            if "print(" in line and ("debug" in line.lower() or "test" in line.lower()):
                issues.append({
                    "type": "debug_print",
                    "message": f"Possible debug print: '{line.strip()}'",
                    "severity": "warning",
                })

        # Check for hardcoded secrets
        for line in added_lines:
            for keyword in ["password", "secret", "api_key", "token"]:
                if keyword in line.lower() and ("=" in line) and not line.strip().startswith("#"):
                    issues.append({
                        "type": "hardcoded_secret",
                        "message": f"Possible hardcoded secret: '{keyword}'",
                        "severity": "error",
                    })

        # Check for TODO/FIXME
        for line in added_lines:
            if "TODO" in line or "FIXME" in line:
                suggestions.append(f"Unresolved marker: {line.strip()}")

        # Check large diff
        if len(added_lines) > 200:
            suggestions.append("Large diff (200+ lines added). Consider splitting into smaller PRs.")

        return {
            "added_lines": len(added_lines),
            "removed_lines": len(removed_lines),
            "hunks": sum(1 for l in lines if l.startswith("@@")),
            "issues": issues,
            "suggestions": suggestions,
            "verdict": "ok" if len(issues) == 0 else "needs_work",
        }
