"""
JARVIS Core Tests.
"""

import pytest
import asyncio

from jarvis.core.orchestrator import Orchestrator, IntentParser, DomainRegistry, Domain, Intent
from jarvis.memory.engine import MemoryEngine
from jarvis.evolution.controller import EvolutionController
from jarvis.sandbox import SandboxManager


class TestIntentParser:
    def setup_method(self):
        self.parser = IntentParser()

    def test_parse_research_intent(self):
        intent = self.parser.parse("搜索最新的AI论文")
        assert intent.primary_domain == Domain.RESEARCH

    def test_parse_engineering_intent(self):
        intent = self.parser.parse("重构这段代码")
        assert intent.primary_domain == Domain.ENGINEERING

    def test_parse_creator_intent(self):
        intent = self.parser.parse("写一个短篇小说")
        assert intent.primary_domain == Domain.CREATOR

    def test_parse_personal_intent(self):
        intent = self.parser.parse("提醒我明天开会")
        assert intent.primary_domain == Domain.PERSONAL

    def test_parse_finance_intent(self):
        intent = self.parser.parse("分析我的投资组合")
        assert intent.primary_domain == Domain.FINANCE

    def test_parse_ambiguous(self):
        intent = self.parser.parse("帮我处理一下")
        assert intent.primary_domain == Domain.PERSONAL

    def test_parse_with_context(self):
        intent = self.parser.parse("优化性能", context=["代码", "构建", "部署"])
        assert intent.primary_domain == Domain.ENGINEERING

    def test_extract_action(self):
        assert self.parser._extract_action("创建新项目") == "创建"
        assert self.parser._extract_action("搜索最新消息") == "搜索"
        assert self.parser._extract_action("你好吗") == "query"


class TestMemoryEngine:
    def setup_method(self):
        import tempfile
        self.temp_dir = tempfile.mkdtemp()
        self.engine = MemoryEngine(persist_dir=self.temp_dir)

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self):
        await self.engine.store("test_key", "Hello JARVIS")
        results = await self.engine.retrieve("Hello")
        assert len(results) > 0
        assert any("JARVIS" in r.content for r in results)

    @pytest.mark.asyncio
    async def test_add_fact(self):
        await self.engine.add_fact("Python is a programming language")
        results = await self.engine.retrieve("programming", entry_types=["fact"])
        assert len(results) > 0


class TestEvolutionController:
    def setup_method(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock
        self.temp_dir = Path(tempfile.mkdtemp())
        self.config = MagicMock()
        self.controller = EvolutionController(self.temp_dir, self.config)

    @pytest.mark.asyncio
    async def test_record_success(self):
        from unittest.mock import MagicMock
        intent = MagicMock()
        intent.raw_text = "test task"
        intent.primary_domain = MagicMock(name="ENGINEERING")
        intent.action = "test"
        result = MagicMock()
        result.success = True
        result.execution_time_ms = 100.0
        result.tokens_consumed = 50

        await self.controller.record_success(intent, result)
        assert len(self.controller.records) == 1

    def test_performance_report_empty(self):
        report = self.controller.get_performance_report()
        assert report["status"] == "No data yet"


class TestSandboxManager:
    def setup_method(self):
        self.sandbox = SandboxManager(engine="local_subprocess")

    def teardown_method(self):
        self.sandbox.cleanup()

    @pytest.mark.asyncio
    async def test_execute_simple_command(self):
        result = await self.sandbox.execute_command("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_execute_python(self):
        result = await self.sandbox.execute_python("print('JARVIS test')")
        assert result.exit_code == 0
        assert "JARVIS test" in result.stdout


class TestDomainRegistry:
    def test_register_and_get(self):
        registry = DomainRegistry()
        mock_module = type("MockModule", (), {"handle": lambda self, x: None})()
        registry.register(Domain.ENGINEERING, mock_module)
        assert registry.get(Domain.ENGINEERING) is not None
        assert registry.get(Domain.PERSONAL) is None

    def test_list_domains(self):
        registry = DomainRegistry()
        registry.register(Domain.PERSONAL, object())
        registry.register(Domain.RESEARCH, object())
        assert len(registry.list_domains()) == 2
