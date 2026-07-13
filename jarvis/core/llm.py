"""
JARVIS LLM Integration Layer.

Provides a unified async interface for all domain handlers to invoke LLMs.
Supports:
- LiteLLM (OpenAI, Anthropic, etc.) when API keys are available
- Intelligent mock fallback when no keys are configured
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("jarvis.llm")


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024
    mock_mode: bool = True


class LLMEngine:
    """Unified LLM invocation layer with mock fallback."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.mock_mode = not config.api_key
        if self.mock_mode:
            logger.info("LLM running in MOCK mode (no API key configured)")
        else:
            logger.info(f"LLM running in LIVE mode: {config.provider}/{config.model}")

    async def complete(
        self,
        prompt: str,
        system: str = "",
        domain: str = "general",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Execute a completion and return the text response.

        Args:
            prompt: The user/domain-level prompt
            system: Optional system message
            domain: Domain identifier for mock routing
            temperature: Override default temperature
            max_tokens: Override default max tokens
        """
        if self.mock_mode:
            return self._mock_complete(prompt, domain, system)
        else:
            return await self._litellm_complete(
                prompt, system, temperature or self.config.temperature, max_tokens or self.config.max_tokens
            )

    async def _litellm_complete(self, prompt: str, system: str, temperature: float, max_tokens: int) -> str:
        """Real LLM invocation via LiteLLM."""
        import litellm

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        model_id = f"{self.config.provider}/{self.config.model}"
        try:
            response = await litellm.acompletion(
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=self.config.api_key,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"LLM call failed: {e}, falling back to mock")
            return self._mock_complete(prompt, "general", system)

    # ------------------------------------------------------------------
    # Mock fallback: domain-aware template responses
    # ------------------------------------------------------------------

    def _mock_complete(self, prompt: str, domain: str, system: str = "") -> str:
        """Generate a domain-aware mock response.

        The mock is not random noise — it produces structured, readable
        responses that demonstrate correct intent understanding.
        """
        handler = getattr(self, f"_mock_{domain}", None)
        if handler:
            return handler(prompt)
        return self._mock_general(prompt)

    # --- Domain-specific mocks ---

    def _mock_personal(self, prompt: str) -> str:
        if "提醒" in prompt:
            return f"[PERSONAL] 已为您设置提醒：「{prompt}」。届时将通过通知提醒您。"
        if "待办" in prompt or "todo" in prompt.lower():
            return f"[PERSONAL] 已添加待办事项：「{prompt}」。当前待办列表已更新。"
        if "日程" in prompt or "会议" in prompt:
            return f"[PERSONAL] 已记录日程：「{prompt}」。已同步到日历。"
        if "笔记" in prompt or "记录" in prompt:
            return f"[PERSONAL] 笔记已保存：「{prompt}」。"
        return f"[PERSONAL] 已收到您的请求：「{prompt}」。我会妥善处理。"

    def _mock_research(self, prompt: str) -> str:
        return (
            f"[RESEARCH] 关于「{prompt}」的研究结果：\n\n"
            f"1. **关键发现**: 该领域近期有显著进展，多篇顶会论文涉及此主题。\n"
            f"2. **核心论文**: 建议查阅 NeurIPS/ICML/ACL 近两年的相关论文。\n"
            f"3. **趋势分析**: 该方向呈现跨学科融合趋势，值得深入关注。\n"
            f"4. **工具推荐**: 可使用 Semantic Scholar / arXiv 进一步检索。\n\n"
            f"—— 以上为基于知识库的初步检索结果。如需深度调研，可指定更具体的子方向。"
        )

    def _mock_engineering(self, prompt: str) -> str:
        code_snippets = {
            "冒泡": "def bubble_sort(arr):\n    n = len(arr)\n    for i in range(n):\n        swapped = False\n        for j in range(n - i - 1):\n            if arr[j] > arr[j + 1]:\n                arr[j], arr[j + 1] = arr[j + 1], arr[j]\n                swapped = True\n        if not swapped:\n            break\n    return arr",
            "排序": "def quicksort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[len(arr) // 2]\n    left = [x for x in arr if x < pivot]\n    middle = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    return quicksort(left) + middle + quicksort(right)",
            "二分": "def binary_search(arr, target):\n    left, right = 0, len(arr) - 1\n    while left <= right:\n        mid = (left + right) // 2\n        if arr[mid] == target:\n            return mid\n        elif arr[mid] < target:\n            left = mid + 1\n        else:\n            right = mid - 1\n    return -1",
            "哈希": "class HashTable:\n    def __init__(self, size=100):\n        self.size = size\n        self.table = [[] for _ in range(size)]\n\n    def _hash(self, key):\n        return hash(key) % self.size\n\n    def put(self, key, value):\n        idx = self._hash(key)\n        for i, (k, v) in enumerate(self.table[idx]):\n            if k == key:\n                self.table[idx][i] = (key, value)\n                return\n        self.table[idx].append((key, value))\n\n    def get(self, key):\n        idx = self._hash(key)\n        for k, v in self.table[idx]:\n            if k == key:\n                return v\n        return None",
        }
        for keyword, code in code_snippets.items():
            if keyword in prompt:
                return f"[ENGINEERING] 根据「{prompt}」，生成的代码实现：\n\n```python\n{code}\n```\n\n时间复杂度与边界情况已在注释中标注。"
        return f"[ENGINEERING] 关于「{prompt}」的分析：\n\n建议采用模块化架构，遵循 SOLID 原则。核心逻辑应独立于 I/O 层，便于单元测试和后续扩展。\n\n```python\n# Implementation stub\ndef solution(*args, **kwargs):\n    # TODO: implement based on requirements\n    pass\n```"

    def _mock_creator(self, prompt: str) -> str:
        if "诗" in prompt:
            return f"[CREATOR] 为您创作：\n\n夏夜\n\n萤火虫提着灯笼\n在稻田间巡逻\n蛙声煮沸了池塘\n星星跌进孩子的梦里\n\n—— 这首短诗源于「{prompt}」的灵感。"
        if "故事" in prompt or "小说" in prompt:
            return f"[CREATOR] 故事开篇（基于「{prompt}」）：\n\n那个夏天的午后，阳光穿过梧桐叶的缝隙，在青石板上洒下斑驳的光影。林远推开那扇锈迹斑斑的铁门时，并没有想到，这个决定会彻底改变他的人生轨迹……\n\n（如需续写，请告知）"
        if "画" in prompt or "设计" in prompt:
            return f"[CREATOR] 视觉方案简述：\n\n主题：「{prompt}」\n- 色调：暖橙色 + 深灰（对比度 7:1）\n- 构图：对角线分割，左上留白\n- 字体：思源黑体 Bold 标题 / Regular 正文\n- 建议尺寸：1920×1080"
        return f"[CREATOR] 已理解你的创作意图：「{prompt}」。建议从以下维度展开：核心概念、视觉风格、受众定位。"

    def _mock_security(self, prompt: str) -> str:
        return (
            f"[SECURITY] 针对「{prompt}」的安全评估：\n\n"
            f"**风险等级: 中**\n\n"
            f"| 检查项 | 状态 | 建议 |\n"
            f"|--------|------|------|\n"
            f"| 端口暴露面 | 需审查 | 关闭非必要端口 |\n"
            f"| 认证机制 | 通过 | 保持多因素认证 |\n"
            f"| 加密传输 | 通过 | TLS 1.3 推荐 |\n"
            f"| 日志审计 | 待完善 | 建议集中化日志管理 |\n"
            f"| 依赖漏洞 | 需扫描 | 运行 `pip-audit` 或 `npm audit` |\n\n"
            f"**优先处理**: 建议立即进行依赖漏洞扫描和端口审计。"
        )

    def _mock_health(self, prompt: str) -> str:
        if "跑" in prompt:
            return f"[HEALTH] 运动记录：已记录「{prompt}」。今日运动目标进度更新，继续保持。建议运动后补充水分和蛋白质。"
        if "睡眠" in prompt:
            return f"[HEALTH] 睡眠记录：已记录「{prompt}」。建议保持 22:00-23:00 入睡，确保 7-8 小时深度睡眠。"
        if "饮食" in prompt or "吃" in prompt:
            return f"[HEALTH] 饮食记录：已记录「{prompt}」。建议均衡摄入碳水、蛋白质和膳食纤维。"
        return f"[HEALTH] 健康记录已保存：「{prompt}」。如需详细分析请提供更多数据。"

    def _mock_finance(self, prompt: str) -> str:
        return (
            f"[FINANCE] 关于「{prompt}」的分析：\n\n"
            f"| 指标 | 当前值 | 变化 |\n"
            f"|------|--------|------|\n"
            f"| 价格 | $185.32 | +2.1% |\n"
            f"| 市值 | $2.89T | -0.3% |\n"
            f"| PE比率 | 32.5 | — |\n"
            f"| 52周最高 | $198.77 | — |\n"
            f"| 52周最低 | $124.30 | — |\n\n"
            f"**免责声明**: 以上为模拟数据，不构成投资建议。实际交易请以实时行情为准。"
        )

    def _mock_home(self, prompt: str) -> str:
        devices = []
        if "灯" in prompt:
            devices.append("灯光已开启（亮度 80%，色温 4000K）")
        if "空调" in prompt or "温度" in prompt:
            devices.append("空调已设为 25°C，节能模式")
        if "窗帘" in prompt:
            devices.append("窗帘已关闭")
        if "电视" in prompt:
            devices.append("电视已打开")
        if devices:
            return f"[HOME] 设备控制结果：\n" + "\n".join(f"  ✓ {d}" for d in devices)
        if "客厅" in prompt:
            return f"[HOME] 客厅场景已激活：「{prompt}」。灯光、空调、窗帘已按预设调整。"
        return f"[HOME] 已执行智能家居指令：「{prompt}」。各设备状态已同步。"

    def _mock_general(self, prompt: str) -> str:
        return f"[CORE] 已理解您的请求：「{prompt}」。\n\n由于当前未配置 LLM API Key，我在 mock 模式下运行。如需启用真实 AI 能力，请设置环境变量 OPENAI_API_KEY 或配置 config.yaml。"


# ---------------------------------------------------------------------------
# Singleton-style accessor for domain handlers
# ---------------------------------------------------------------------------

_llm_instance: Optional[LLMEngine] = None


def get_llm() -> LLMEngine:
    """Return the global LLM engine instance."""
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LLMEngine(LLMConfig())
    return _llm_instance


def init_llm(config_obj: Any) -> LLMEngine:
    """Initialize LLM engine from JARVIS config."""
    global _llm_instance
    llm_config = LLMConfig(
        provider=getattr(getattr(config_obj, "llm", None), "provider", "openai"),
        model=getattr(getattr(config_obj, "llm", None), "model", "gpt-4o"),
        api_key=getattr(getattr(config_obj, "llm", None), "api_key", ""),
        temperature=getattr(getattr(config_obj, "llm", None), "temperature", 0.7),
        max_tokens=getattr(getattr(config_obj, "llm", None), "max_tokens", 1024),
    )
    _llm_instance = LLMEngine(llm_config)
    return _llm_instance
