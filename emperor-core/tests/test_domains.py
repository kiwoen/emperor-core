"""
Domain-level unit tests.

Tests that all 8 domain modules:
1. Can be loaded and initialized
2. Can handle valid intents and return structured TaskResults
3. Extract entities correctly from natural language inputs
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis.core.orchestrator import Intent, Domain, TaskResult


# ─── Test fixtures ─────────────────────────────────────────────


def _make_intent(text, primary_domain):
    return Intent(raw_text=text, primary_domain=primary_domain)


def _load_domain(domain_module_path: str):
    import importlib
    mod = importlib.import_module(domain_module_path)
    return mod.DomainModule(orchestrator=None)


# ─── Research Domain ───────────────────────────────────────────


class TestResearchDomain:
    DOMAIN = Domain.RESEARCH

    @pytest.mark.asyncio
    async def test_search(self):
        dm = _load_domain("jarvis.domains.research")
        intent = _make_intent("搜索量子计算最新论文", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.domain == self.DOMAIN
        assert "search_type" in result.data

    @pytest.mark.asyncio
    async def test_paper(self):
        dm = _load_domain("jarvis.domains.research")
        intent = _make_intent("查找GPT论文", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "arxiv" in result.data["sources"]

    @pytest.mark.asyncio
    async def test_trend(self):
        dm = _load_domain("jarvis.domains.research")
        intent = _make_intent("AI行业趋势分析", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "time_horizon" in result.data

    @pytest.mark.asyncio
    async def test_research_framework(self):
        dm = _load_domain("jarvis.domains.research")
        intent = _make_intent("研究可再生能源技术", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        phases = result.data
        assert "phase_1" in phases


# ─── Engineering Domain ────────────────────────────────────────


class TestEngineeringDomain:
    DOMAIN = Domain.ENGINEERING

    @pytest.mark.asyncio
    async def test_code_gen(self):
        dm = _load_domain("jarvis.domains.engineering")
        intent = _make_intent("写一个Python排序函数", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["language"] == "python"

    @pytest.mark.asyncio
    async def test_debug(self):
        dm = _load_domain("jarvis.domains.engineering")
        intent = _make_intent("修复这段代码的bug", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["sandbox"] is True

    @pytest.mark.asyncio
    async def test_refactor(self):
        dm = _load_domain("jarvis.domains.engineering")
        intent = _make_intent("重构这个模块", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["preserve_tests"] is True

    @pytest.mark.asyncio
    async def test_git(self):
        dm = _load_domain("jarvis.domains.engineering")
        intent = _make_intent("git commit", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["operation"] == "commit"

    @pytest.mark.asyncio
    async def test_architecture(self):
        dm = _load_domain("jarvis.domains.engineering")
        intent = _make_intent("设计微服务架构", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "deliverables" in result.data


# ─── Creator Domain ────────────────────────────────────────────


class TestCreatorDomain:
    DOMAIN = Domain.CREATOR

    @pytest.mark.asyncio
    async def test_writing(self):
        dm = _load_domain("jarvis.domains.creator")
        intent = _make_intent("写一篇科幻小说", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["genre"] == "小说"

    @pytest.mark.asyncio
    async def test_design(self):
        dm = _load_domain("jarvis.domains.creator")
        intent = _make_intent("设计一张科技峰会海报", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["design_type"] == "poster"

    @pytest.mark.asyncio
    async def test_presentation(self):
        dm = _load_domain("jarvis.domains.creator")
        intent = _make_intent("做一个产品演示PPT", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["format"] == "pptx"

    @pytest.mark.asyncio
    async def test_image_prompt(self):
        dm = _load_domain("jarvis.domains.creator")
        intent = _make_intent("画一幅星空插画", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["prompt_style"] == "detailed"


# ─── Personal Domain ───────────────────────────────────────────


class TestPersonalDomain:
    DOMAIN = Domain.PERSONAL

    @pytest.mark.asyncio
    async def test_reminder(self):
        dm = _load_domain("jarvis.domains.personal")
        intent = _make_intent("提醒我下午3点开会", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["type"] == "reminder"

    @pytest.mark.asyncio
    async def test_todo(self):
        dm = _load_domain("jarvis.domains.personal")
        intent = _make_intent("添加待办：完成周报", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_note(self):
        dm = _load_domain("jarvis.domains.personal")
        intent = _make_intent("记录笔记：JARVIS架构设计要点", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "note_key" in result.data

    @pytest.mark.asyncio
    async def test_plan(self):
        dm = _load_domain("jarvis.domains.personal")
        intent = _make_intent("制定明天的学习计划", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "time_blocks" in result.data

    @pytest.mark.asyncio
    async def test_focus(self):
        dm = _load_domain("jarvis.domains.personal")
        intent = _make_intent("开始25分钟专注番茄钟", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["method"] == "pomodoro"


# ─── Security Domain ───────────────────────────────────────────


class TestSecurityDomain:
    DOMAIN = Domain.SECURITY

    @pytest.mark.asyncio
    async def test_scan(self):
        dm = _load_domain("jarvis.domains.security")
        intent = _make_intent("扫描系统端口", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "ports" in result.data["targets"]

    @pytest.mark.asyncio
    async def test_monitor(self):
        dm = _load_domain("jarvis.domains.security")
        intent = _make_intent("开启系统监控", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "cpu" in result.data["metrics"]

    @pytest.mark.asyncio
    async def test_encrypt(self):
        dm = _load_domain("jarvis.domains.security")
        intent = _make_intent("加密敏感文件", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["algorithm"] == "AES-256-GCM"

    @pytest.mark.asyncio
    async def test_vuln(self):
        dm = _load_domain("jarvis.domains.security")
        intent = _make_intent("扫描系统漏洞", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["severity_filter"] == "high_and_critical"


# ─── Health Domain ─────────────────────────────────────────────


class TestHealthDomain:
    DOMAIN = Domain.HEALTH

    @pytest.mark.asyncio
    async def test_exercise(self):
        dm = _load_domain("jarvis.domains.health")
        intent = _make_intent("制定跑步运动计划", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["type"] == "跑步"

    @pytest.mark.asyncio
    async def test_sleep(self):
        dm = _load_domain("jarvis.domains.health")
        intent = _make_intent("分析我的睡眠质量", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "duration" in result.data["metrics"]

    @pytest.mark.asyncio
    async def test_diet(self):
        dm = _load_domain("jarvis.domains.health")
        intent = _make_intent("制定素食减脂食谱", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "vegan" in result.data["restrictions"]

    @pytest.mark.asyncio
    async def test_meditation(self):
        dm = _load_domain("jarvis.domains.health")
        intent = _make_intent("引导10分钟冥想", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["type"] == "guided_breathing"


# ─── Finance Domain ────────────────────────────────────────────


class TestFinanceDomain:
    DOMAIN = Domain.FINANCE

    @pytest.mark.asyncio
    async def test_stock(self):
        dm = _load_domain("jarvis.domains.finance")
        intent = _make_intent("分析A股市场最近走势", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["exchange"] == "SSE_SZSE"

    @pytest.mark.asyncio
    async def test_budget(self):
        dm = _load_domain("jarvis.domains.finance")
        intent = _make_intent("制定月度预算计划", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["method"] == "50_30_20"

    @pytest.mark.asyncio
    async def test_crypto(self):
        dm = _load_domain("jarvis.domains.finance")
        intent = _make_intent("查看比特币和以太坊行情", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "bitcoin" in result.data["assets"]

    @pytest.mark.asyncio
    async def test_portfolio(self):
        dm = _load_domain("jarvis.domains.finance")
        intent = _make_intent("分析我的投资组合", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert "sharpe" in result.data["metrics"]


# ─── Home Domain ───────────────────────────────────────────────


class TestHomeDomain:
    DOMAIN = Domain.HOME

    @pytest.mark.asyncio
    async def test_light(self):
        dm = _load_domain("jarvis.domains.home")
        intent = _make_intent("打开客厅灯光到50%亮度", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["action"] == "on"
        assert result.data["device"] == "客厅"

    @pytest.mark.asyncio
    async def test_climate(self):
        dm = _load_domain("jarvis.domains.home")
        intent = _make_intent("把卧室空调调到26度", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["target_temp"] == 26
        assert result.data["room"] == "卧室"

    @pytest.mark.asyncio
    async def test_scene(self):
        dm = _load_domain("jarvis.domains.home")
        intent = _make_intent("激活睡眠模式", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["scene"] == "sleep"

    @pytest.mark.asyncio
    async def test_energy(self):
        dm = _load_domain("jarvis.domains.home")
        intent = _make_intent("查看本月能源消耗", self.DOMAIN)
        result = await dm.handle(intent)
        assert result.success
        assert result.data["period"] == "monthly"
