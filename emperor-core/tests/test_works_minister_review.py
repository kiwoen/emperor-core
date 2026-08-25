"""Tests for the code-review dispatch loop: 工部尚书 → CodeReviewer.

验证「多维代码审查（script-multi-review 模式）」已接入大臣派发闭环：
code-review 意图经 TaskRouter 分类为 code 类，并路由到工部尚书，
工部 `_handle` 检测到审查意图+代码即调用 CodeReviewer 返回八维加权报告。
"""
import pytest

from huanxin.core.task_router import classify_task_type, route_to_minister
from huanxin.court.ministers.works import WorksMinister, _extract_code
from huanxin.court.minister import Edict
from huanxin.court.ministers import create_ministers


REVIEW_INTENT = (
    "帮我审查这段代码：\n"
    "```python\n"
    "import os\n"
    "password = \"hardcoded123\"\n"
    "def process(data, config=[]):\n"
    "    try:\n"
    "        return data\n"
    "    except:\n"
    "        pass\n"
    "```"
)


# ============================================================================
# TaskRouter: 审查意图分类
# ============================================================================

class TestClassifyReview:
    def test_review_keywords_classified_as_code(self):
        assert classify_task_type("帮我做代码审查") == "code"
        assert classify_task_type("审查一下这段代码") == "code"
        assert classify_task_type("please code review this") == "code"

    def test_route_picks_works_minister_for_review(self):
        ministers = create_ministers()
        edict = Edict(edict_id="t1", intent=REVIEW_INTENT)
        minister, score = route_to_minister(edict, ministers)
        assert minister is not None
        assert minister.name == "工部尚书"
        assert score > 0.0


# ============================================================================
# WorksMinister._handle → CodeReviewer 闭环
# ============================================================================

class TestWorksMinisterReview:
    @pytest.mark.asyncio
    async def test_handle_routes_to_code_reviewer(self):
        m = WorksMinister()
        memorial_out = await m._handle(Edict(edict_id="e1", intent=REVIEW_INTENT))
        output, confidence = memorial_out
        # 八维审查报告特征
        assert "八维评分" in output or "代码多维审查报告" in output
        assert "🔴" in output  # 严重度图标
        assert confidence >= 0.9  # 确定性引擎给出高置信

    @pytest.mark.asyncio
    async def test_handle_finds_hardcoded_secret(self):
        m = WorksMinister()
        output, _ = await m._handle(Edict(edict_id="e2", intent=REVIEW_INTENT))
        # 硬编码密钥应被八维引擎捕获
        assert "hardcoded" in output.lower() or "密钥" in output or "secret" in output.lower()

    @pytest.mark.asyncio
    async def test_handle_non_review_falls_back(self):
        m = WorksMinister()
        out, conf = await m._handle(Edict(edict_id="e3", intent="帮我写一个快排函数"))
        # 非审查意图 → 回退工程建议，不应是审查报告
        assert "营造录" in out
        assert "代码多维审查报告" not in out

    def test_extract_code_fenced(self):
        code = _extract_code(REVIEW_INTENT)
        assert code is not None
        assert "password" in code

    def test_extract_code_plain(self):
        plain = "def foo():\n    return 1\n"
        assert _extract_code(plain) == plain.strip()
