"""Tests for Codex engine, analyzer, and generator."""
import asyncio
import tempfile
from pathlib import Path

import pytest

from jarvis.hermes.bus import MessageBus, Topic
from jarvis.hermes.event_log import EventLog
from jarvis.codex.engine import CodexEngine
from jarvis.codex.analyzer import Analyzer
from jarvis.codex.generator import Generator


# ============================================================================
# Sample data
# ============================================================================

SAMPLE_PYTHON = '''
"""Sample module."""
import os
import sys
from typing import Optional


class Calculator:
    """Simple calculator."""

    def add(self, a: int, b: int) -> int:
        return a + b

    def divide(self, a: int, b: int) -> Optional[float]:
        if b == 0:
            return None
        return a / b


def process_data(
    source: str,
    dest: str,
    mode: str,
    verbose: bool,
    dry_run: bool,
    force: bool,
    backup: bool,
) -> None:
    """Too many args demo."""
    if verbose:
        print("Starting...")
    for i in range(10):
        if i % 2 == 0:
            print(i)
    print("Done")


# TODO: refactor this
print("debug hello")
'''

SAMPLE_DIFF = """diff --git a/app.py b/app.py
index 123..456 100644
--- a/app.py
+++ b/app.py
@@ -1,5 +1,8 @@
 import os
+import json
+
+password = "hardcoded123"
 
-def main():
+def main(config=None):
+    print("debug: starting")
     pass
"""


# ============================================================================
# Analyzer
# ============================================================================

class TestAnalyzer:
    def test_analyze_python_basic(self):
        a = Analyzer()
        result = a.analyze({"code": SAMPLE_PYTHON, "language": "python"})
        assert result["language"] == "python"
        assert result["lines"] > 0
        assert len(result["functions"]) >= 1
        assert len(result["classes"]) >= 1
        assert len(result["imports"]) >= 2

    def test_analyze_finds_class(self):
        a = Analyzer()
        result = a.analyze({"code": SAMPLE_PYTHON, "language": "python"})
        class_names = [c["name"] for c in result["classes"]]
        assert "Calculator" in class_names

    def test_analyze_finds_functions(self):
        a = Analyzer()
        result = a.analyze({"code": SAMPLE_PYTHON, "language": "python"})
        func_names = [f["name"] for f in result["functions"]]
        assert "process_data" in func_names
        assert "add" in func_names

    def test_analyze_detects_too_many_args(self):
        a = Analyzer()
        result = a.analyze({"code": SAMPLE_PYTHON, "language": "python"})
        issues = [i for i in result["issues"] if i["type"] == "too_many_args"]
        assert len(issues) >= 1

    def test_analyze_complexity(self):
        a = Analyzer()
        result = a.analyze({"code": SAMPLE_PYTHON, "language": "python"})
        c = result["complexity"]
        assert "total_cyclomatic" in c
        assert "rating" in c
        assert c["total_cyclomatic"] > 0

    def test_analyze_detects_long_function(self):
        lines = ["def long_func():"]
        lines.extend([f"    x = {i}" for i in range(60)])
        code = "\n".join(lines)
        a = Analyzer()
        result = a.analyze({"code": code, "language": "python"})
        issues = [i for i in result["issues"] if i["type"] == "long_function"]
        assert len(issues) >= 1

    def test_analyze_syntax_error(self):
        a = Analyzer()
        result = a.analyze({"code": "def foo(:", "language": "python"})
        assert any(i["type"] == "syntax_error" for i in result["issues"])

    def test_analyze_empty_code(self):
        a = Analyzer()
        result = a.analyze({"code": "", "language": "python"})
        assert result["language"] == "python"
        assert result["lines"] == 0

    def test_analyze_generic_language(self):
        a = Analyzer()
        result = a.analyze({"code": "console.log('hi');", "language": "javascript"})
        assert result["language"] == "javascript"
        assert result["lines"] == 1

    def test_analyze_bad_payload(self):
        a = Analyzer()
        result = a.analyze("not a dict")
        assert "error" in result

    def test_review_diff(self):
        a = Analyzer()
        result = a.review_diff(SAMPLE_DIFF)
        assert result["added_lines"] > 0
        assert len(result["issues"]) >= 1

    def test_review_diff_detects_secret(self):
        a = Analyzer()
        result = a.review_diff(SAMPLE_DIFF)
        secrets = [i for i in result["issues"] if i["type"] == "hardcoded_secret"]
        assert len(secrets) >= 1

    def test_review_diff_empty(self):
        a = Analyzer()
        result = a.review_diff("")
        assert result["added_lines"] == 0


# ============================================================================
# Generator
# ============================================================================

class TestGenerator:
    def test_generate_python_module(self):
        g = Generator()
        result = g.generate({"template": "python_module", "params": {
            "module_name": "my_module", "description": "Test module"
        }})
        assert "my_module" in result["code"]
        assert 'if __name__ == "__main__"' in result["code"]

    def test_generate_python_class(self):
        g = Generator()
        result = g.generate({"template": "python_class", "params": {
            "class_name": "MyClass", "description": "A test class"
        }})
        assert "class MyClass" in result["code"]

    def test_generate_python_test(self):
        g = Generator()
        result = g.generate({"template": "python_test", "params": {
            "module_name": "mymod", "class_name": "MyClass"
        }})
        assert "class TestMyClass" in result["code"]
        assert "from mymod import MyClass" in result["code"]

    def test_generate_fastapi(self):
        g = Generator()
        result = g.generate({"template": "python_fastapi", "params": {"title": "MyAPI"}})
        assert "MyAPI" in result["code"]
        assert "FastAPI" in result["code"]

    def test_generate_cli(self):
        g = Generator()
        result = g.generate({"template": "python_cli", "params": {"description": "My Tool"}})
        assert "argparse" in result["code"]

    def test_generate_missing_params(self):
        g = Generator()
        result = g.generate({"template": "python_class", "params": {}})
        assert "error" in result

    def test_generate_unknown_template(self):
        g = Generator()
        result = g.generate({"template": "nonexistent", "params": {}})
        assert "error" in result
        assert "available_templates" in result

    def test_refactor_cleanup(self):
        g = Generator()
        code = "def foo():  \n    pass  \n\n\n\n\ndef bar():\n    pass"
        result = g.refactor(code, "cleanup")
        assert result["changed"]
        cleaned = result["code"]
        assert "  \n" not in cleaned

    def test_refactor_cleanup_no_change(self):
        g = Generator()
        code = "def foo():\n    pass"
        result = g.refactor(code, "cleanup")
        assert not result["changed"]

    def test_refactor_rename_missing_params(self):
        g = Generator()
        result = g.refactor("code", "rename")
        assert "error" in result

    def test_refactor_extract_placeholder(self):
        g = Generator()
        result = g.refactor("def foo():\n    pass", "extract")
        assert not result["changed"]
        assert "note" in result

    def test_refactor_unknown_pattern(self):
        g = Generator()
        result = g.refactor("code", "unknown")
        assert "error" in result


# ============================================================================
# CodexEngine (Hermes integration)
# ============================================================================

class TestCodexEngine:
    @pytest.mark.asyncio
    async def test_start_shutdown(self):
        bus = MessageBus()
        engine = CodexEngine(bus, Analyzer(), Generator())
        await engine.start()
        assert engine._running is True
        await engine.shutdown()
        assert engine._running is False

    @pytest.mark.asyncio
    async def test_analyze_via_bus(self):
        bus = MessageBus()
        engine = CodexEngine(bus, Analyzer(), Generator())
        await engine.start()

        reply = await bus.request(
            Topic("codex.analyze.python"),
            payload={"code": "def foo():\n    return 1\n", "language": "python"},
            sender="test",
            timeout=3.0,
        )
        assert reply.payload["language"] == "python"
        assert len(reply.payload["functions"]) == 1
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_generate_via_bus(self):
        bus = MessageBus()
        engine = CodexEngine(bus, Analyzer(), Generator())
        await engine.start()

        reply = await bus.request(
            Topic("codex.generate.python"),
            payload={"template": "python_class", "params": {"class_name": "Foo", "description": "Bar"}},
            sender="test",
            timeout=3.0,
        )
        assert "class Foo" in reply.payload["code"]
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_review_via_bus(self):
        bus = MessageBus()
        engine = CodexEngine(bus, Analyzer(), Generator())
        await engine.start()

        reply = await bus.request(
            Topic("codex.review.diff"),
            payload={"diff": SAMPLE_DIFF},
            sender="test",
            timeout=3.0,
        )
        assert reply.payload["verdict"] == "needs_work"
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_error_handling(self):
        bus = MessageBus()
        engine = CodexEngine(bus, Analyzer(), Generator())
        await engine.start()

        reply = await bus.request(
            Topic("codex.analyze.python"),
            payload="bad payload",
            sender="test",
            timeout=3.0,
        )
        assert "error" in reply.payload
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_unknown_topic(self):
        bus = MessageBus()
        engine = CodexEngine(bus, Analyzer(), Generator())
        await engine.start()

        reply = await bus.request(
            Topic("codex.unknown.thing"),
            payload={},
            sender="test",
            timeout=3.0,
        )
        assert "error" in reply.payload
        await engine.shutdown()


# ============================================================================
# Full Hermes + Codex integration
# ============================================================================

class TestHermesCodexIntegration:
    @pytest.mark.asyncio
    async def test_full_flow(self):
        """End-to-end: Orchestrator -> Hermes -> Codex -> reply -> event log."""
        log_dir = tempfile.mkdtemp()
        event_log = EventLog(str(Path(log_dir) / "events.log"))
        await event_log.start()

        bus = MessageBus(event_log=event_log)
        engine = CodexEngine(bus, Analyzer(), Generator())
        await engine.start()

        reply = await bus.request(
            Topic("codex.analyze.python"),
            payload={"code": SAMPLE_PYTHON, "language": "python"},
            sender="orchestrator",
            timeout=3.0,
        )
        assert reply.payload["language"] == "python"
        assert reply.sender == "codex"

        reply = await bus.request(
            Topic("codex.generate.python"),
            payload={"template": "python_module", "params": {"module_name": "test", "description": "Test"}},
            sender="orchestrator",
            timeout=3.0,
        )
        assert "test" in reply.payload["code"]

        await engine.shutdown()
        await asyncio.sleep(0.6)
        await event_log.shutdown()

        events = event_log.tail(20)
        topics = [e["topic"] for e in events]
        assert "codex.analyze.python" in topics
        assert "codex.generate.python" in topics

    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Multiple concurrent requests should all succeed."""
        log_dir = tempfile.mkdtemp()
        event_log = EventLog(str(Path(log_dir) / "events.log"))
        await event_log.start()

        bus = MessageBus(event_log=event_log)
        engine = CodexEngine(bus, Analyzer(), Generator())
        await engine.start()

        async def send_analyze(i: int):
            return await bus.request(
                Topic("codex.analyze.python"),
                payload={"code": f"def func{i}():\n    return {i}\n", "language": "python"},
                sender=f"client_{i}",
                timeout=5.0,
            )

        replies = await asyncio.gather(*[send_analyze(i) for i in range(10)])
        for i, reply in enumerate(replies):
            assert reply.payload["language"] == "python"
            funcs = reply.payload.get("functions", [])
            assert any(f"func{i}" == f["name"] for f in funcs)

        await engine.shutdown()
        await asyncio.sleep(0.6)
        await event_log.shutdown()
        assert event_log.line_count >= 10  # 10 requests published
