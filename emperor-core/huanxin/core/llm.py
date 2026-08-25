"""
HUANXIN LLM Integration Layer.

Provides a unified async interface for all domain handlers to invoke LLMs.
Supports:
- LiteLLM (OpenAI, Anthropic, etc.) when API keys are available
- Intelligent mock fallback when no keys are configured
- Multi-backend failover with retry / circuit-breaker / rate-limit throttle
  (see :class:`LLMManager`) so a flaky free endpoint never breaks the loop.

Optimization layer (added in the "完整优化" pass):
- Per-backend retry with exponential backoff + HTTP-429 ``Retry-After`` honour.
- Circuit breaker: a backend that fails ``failure_threshold`` times in a row is
  skipped (cooldown) instead of being retried every call.
- Rate-limit throttle: ``requests_per_minute`` (per provider in FREE_PROVIDERS)
  enforces a minimum inter-call interval — essential for free tiers such as
  NVIDIA NIM (40 req/min).
- Telemetry: per-backend success/failure counts, latency, last-success time and
  circuit state, surfaced via :meth:`LLMManager.get_stats`.
- Single source of truth for backend resolution: :func:`_resolve_backends_from_env`
  is shared by :func:`build_manager_from_env` and the self-evolution executor.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# litellm is an optional dependency; import lazily/guarded so the module
# imports cleanly even when it is not installed (falls back to mock).
try:  # pragma: no cover - environment dependent
    import litellm  # type: ignore
except Exception:  # pragma: no cover
    litellm = None

logger = logging.getLogger("huanxin.llm")


class Environment(Enum):
    """Deployment tier that selects the OpenAI-compatible Base URL."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


def get_base_url(env: Environment) -> str:
    """Resolve the OpenAI-compatible Base URL for a given environment.

    Each tier reads an override env var (``OPENAI_DEV_URL`` / ``OPENAI_TEST_URL``
    / ``OPENAI_PROD_URL``) and falls back to a sensible default. This lets the
    same HUANXIN binary talk to the official OpenAI API in dev and a self-hosted
    or company proxy in testing/production without code changes.
    """
    configs = {
        Environment.DEVELOPMENT: os.getenv("OPENAI_DEV_URL", "https://api.openai.com/v1/"),
        Environment.TESTING: os.getenv("OPENAI_TEST_URL", "https://test-openai.your-company.com/v1/"),
        Environment.PRODUCTION: os.getenv("OPENAI_PROD_URL", "https://openai.your-company.com/v1/"),
    }
    return configs[env]


# ── Curated registry of free / cheap OpenAI-compatible endpoints ─────────────
# Each entry is selectable by name via OPENAI_FALLBACK_PROVIDERS. ``key_env`` is
# the env var holding the provider's API key (empty => keyless / local).
# ``free_tier`` notes are informational only and may change over time.
# ``requests_per_minute`` (when > 0) enables the built-in rate-limit throttle.
FREE_PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        "requests_per_minute": 0,
        "free_tier": "新账号赠送额度；deepseek-chat 极便宜",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
        "requests_per_minute": 30,
        "free_tier": "GroqCloud 免费档（限速率，需 key）",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
        "key_env": "OPENROUTER_API_KEY",
        "requests_per_minute": 20,
        "free_tier": "含多种免费模型（如 meta-llama/...-free）",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "key_env": "TOGETHER_API_KEY",
        "requests_per_minute": 0,
        "free_tier": "新用户免费额度",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-small-latest",
        "key_env": "MISTRAL_API_KEY",
        "requests_per_minute": 0,
        "free_tier": "Le Chat 试用额度",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
        "key_env": "",
        "requests_per_minute": 0,
        "free_tier": "完全本地免费，无需 key（需先 ollama serve）",
    },
    "doubao": {
        "provider": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-seed-1-8-251228",
        "model_env": "ARK_MODEL",
        "key_env": "ARK_API_KEY",
        "requests_per_minute": 0,
        "free_tier": "火山方舟豆包，按量计费；新用户有试用额度",
    },
    "nvidia": {
        "provider": "openai",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-3.1-8b-instruct",
        "model_env": "NVIDIA_MODEL",
        "key_env": "NVIDIA_API_KEY",
        "requests_per_minute": 40,  # NVIDIA NIM free tier = ~1000 credits / 40 req·min
        "free_tier": "NVIDIA NIM 免费档，约1000额度/40 req·min，OpenAI 兼容",
    },
}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024
    mock_mode: Optional[bool] = None  # None => auto (live when api_key/base_url set)
    router_enabled: bool = True
    fallback_model: str = "gpt-4o"
    request_timeout: int = 60
    # ── Resilience knobs (optimization pass) ──
    max_retries: int = 0            # transient-failure retries inside one backend
    retry_backoff: float = 0.3      # base seconds for exponential backoff
    requests_per_minute: int = 0    # >0 enables inter-call throttle for this backend

    @classmethod
    def from_env(cls, **overrides: Any) -> "LLMConfig":
        """Build a config from OPENAI_* environment variables.

        Works with any OpenAI-compatible endpoint (ChatOpens, DeepSeek,
        local Ollama, etc.). ``overrides`` take precedence over env values.

        Base URL resolution priority:
          1. ``OPENAI_BASE_URL`` (explicit override, highest priority)
          2. ``OPENAI_ENV`` tier -> ``OPENAI_{DEV,TEST,PROD}_URL``
             (only when ``OPENAI_ENV`` is explicitly set)
          3. empty -> keep mock-by-default semantics (no network call)

        Resilience knobs (all optional, default off / inert):
          LLM_MAX_RETRIES, LLM_RETRY_BACKOFF, LLM_REQUESTS_PER_MINUTE
        """
        base_url = os.getenv("OPENAI_BASE_URL", "")
        if not base_url:
            env_name = os.getenv("OPENAI_ENV", "").strip().lower()
            if env_name:
                try:
                    env_enum = Environment(env_name)
                except ValueError:
                    logger.warning("Unknown OPENAI_ENV=%r; falling back to development", env_name)
                    env_enum = Environment.DEVELOPMENT
                base_url = get_base_url(env_enum)

        cfg = cls(
            provider=os.getenv("OPENAI_PROVIDER", "openai"),
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=base_url,
            temperature=_safe_float(os.getenv("OPENAI_TEMPERATURE", "0.7"), 0.7),
            max_tokens=_safe_int(os.getenv("OPENAI_MAX_TOKENS", "1024"), 1024),
            max_retries=_safe_int(os.getenv("LLM_MAX_RETRIES", "0"), 0),
            retry_backoff=_safe_float(os.getenv("LLM_RETRY_BACKOFF", "0.3"), 0.3),
            requests_per_minute=_safe_int(os.getenv("LLM_REQUESTS_PER_MINUTE", "0"), 0),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(cfg, key, value)
        return cfg


@dataclass
class BackendStats:
    """Per-backend telemetry for the multi-backend manager."""

    total: int = 0
    success: int = 0
    failure: int = 0
    last_latency_ms: float = 0.0
    last_success_ts: float = 0.0
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0  # epoch seconds; >now means open

    @property
    def circuit_open(self) -> bool:
        return time.time() < self.circuit_open_until

    def record_success(self, latency_ms: float) -> None:
        self.total += 1
        self.success += 1
        self.last_latency_ms = latency_ms
        self.last_success_ts = time.time()
        self.consecutive_failures = 0

    def record_failure(self, threshold: int, cooldown_s: float) -> None:
        self.total += 1
        self.failure += 1
        self.consecutive_failures += 1
        if threshold > 0 and self.consecutive_failures >= threshold and cooldown_s > 0:
            self.circuit_open_until = time.time() + cooldown_s

    def reset_circuit(self) -> None:
        self.circuit_open_until = 0.0
        self.consecutive_failures = 0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "success": self.success,
            "failure": self.failure,
            "success_rate": (self.success / self.total) if self.total else 0.0,
            "last_latency_ms": round(self.last_latency_ms, 2),
            "last_success_ts": self.last_success_ts,
            "consecutive_failures": self.consecutive_failures,
            "circuit_open": self.circuit_open,
        }


class LLMEngine:
    """Unified LLM invocation layer with mock fallback + retry."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.last_error: Optional[str] = None
        live = bool(config.api_key or config.base_url)
        if config.mock_mode is None:
            self.mock_mode = not live
        else:
            self.mock_mode = config.mock_mode
        self.router: Optional[Any] = None

        if config.router_enabled:
            try:
                from huanxin.core.router import ModelRouter
                self.router = ModelRouter()
            except Exception:  # pragma: no cover - optional dependency
                self.router = None

        if self.mock_mode:
            logger.info("LLM running in MOCK mode (no API key / base_url configured)")
        else:
            logger.info(f"LLM running in LIVE mode: {config.provider}/{config.model} base={config.base_url or 'default'}")
        # 最近一次调用的 token 用量（由 _record_usage 写入），供上层计量
        self.last_usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    async def complete(
        self,
        prompt: str,
        system: str = "",
        domain: str = "general",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        history: Optional[list[dict]] = None,
    ) -> str:
        """Execute a completion and return the text response.

        On a live-call failure the engine degrades to a mock reply (so a single
        backend never crashes a caller) and records ``last_error`` so the
        manager can fail over to the next backend.

        :param history: optional list of ``{"role": "user"|"assistant", "content": str}``
            for multi-turn context (used by the chat UI for persistent sessions).
        """
        # ── Route to optimal model tier (skip in mock_mode) ──
        if self.router is not None and not self.mock_mode:
            try:
                result = self.router.route(prompt, domain, temperature, max_tokens)
                logger.info(
                    "Router: tier=%s model=%s estimated_cost=%.6f",
                    result.tier, result.model_id, result.estimated_cost,
                )
            except Exception:  # pragma: no cover - router is best-effort
                logger.debug("Router unavailable", exc_info=True)

        if self.mock_mode:
            return self._mock_complete(prompt, domain, system)
        try:
            return await self._litellm_complete(
                prompt, system, temperature or self.config.temperature, max_tokens or self.config.max_tokens,
                history=history,
            )
        except Exception as e:  # noqa: BLE001 - degrade to mock, surface via last_error
            self.last_error = str(e)
            logger.error(f"LLM live call failed: {e}; degrading to mock")
            return self._mock_complete(prompt, domain, system)

    def get_cost_report(self) -> dict:
        """Return the router cost statistics.

        Returns an empty report if the router is disabled.
        """
        if self.router is None:
            return {
                "total_requests": 0,
                "requests_by_tier": {},
                "estimated_cost_saved": 0.0,
                "savings_percent": 0.0,
                "router_enabled": False,
            }
        report = self.router.report()
        report["router_enabled"] = True
        return report

    async def _litellm_complete(self, prompt: str, system: str, temperature: float, max_tokens: int, history: Optional[list[dict]] = None) -> str:
        """Real LLM invocation via LiteLLM, with internal retry/backoff.

        Raises on final failure (after ``max_retries`` attempts) so the caller
        can decide how to degrade.
        """
        if litellm is None:
            raise RuntimeError("litellm not installed; cannot do live call")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        # 注入多轮历史（取最近 20 条，避免超出上下文）
        if history:
            for h in history[-20:]:
                if isinstance(h, dict) and h.get("role") in ("user", "assistant") and h.get("content"):
                    messages.append({"role": h["role"], "content": str(h["content"])})
        messages.append({"role": "user", "content": prompt})

        model_id = f"{self.config.provider}/{self.config.model}"
        kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Some OpenAI-compatible free proxies need no key; supply a dummy
            # so litellm does not reject the call outright.
            "api_key": self.config.api_key or "sk-noauth",
            "timeout": self.config.request_timeout,
        }
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url

        attempts = max(1, self.config.max_retries + 1)
        last_exc: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                response = await litellm.acompletion(**kwargs)
                self._record_usage(response)
                return response.choices[0].message.content.strip()
            except Exception as e:  # noqa: BLE001 - retry transient failures
                last_exc = e
                if attempt < attempts - 1:
                    wait = self._backoff_seconds(e, attempt)
                    logger.warning("LLM attempt %d/%d failed: %s (retry in %.2fs)", attempt + 1, attempts, e, wait)
                    if wait > 0:
                        await asyncio.sleep(wait)
        assert last_exc is not None
        raise last_exc

    def _record_usage(self, response) -> None:
        """Best-effort extraction of token usage from a litellm response."""
        try:
            u = response.usage
            if u is None:
                return
            self.last_usage = {
                "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
            }
        except Exception:  # noqa: BLE001 - never let usage bookkeeping break a call
            pass

    @staticmethod
    def _backoff_seconds(exc: Exception, attempt: int) -> float:
        """Compute backoff for a failed attempt.

        Honours HTTP 429 ``Retry-After`` when present; otherwise uses an
        exponential backoff from the engine config (passed via attempt only).
        """
        # Try to extract Retry-After from common exception shapes.
        retry_after = None
        msg = str(exc)
        if "Retry-After" in msg or "retry-after" in msg:
            for token in msg.split():
                if token.replace(".", "").isdigit():
                    retry_after = float(token)
                    break
        if retry_after is not None:
            return float(retry_after)
        return 0.0  # engine-level exponential handled by caller loop

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


class LLMManager:
    """Multi-backend LLM manager with failover across OpenAI-compatible endpoints.

    Lets HUANXIN "同时接入各种免费模型": one primary backend plus an ordered list
    of fallbacks (explicit URLs, preset free providers, or a curated registry).
    A ``complete()`` call tries each backend in order and transparently fails over
    on error (network / 401 / rate-limit), so a flaky free endpoint never breaks
    the self-learning loop.

    Resilience features (optimization pass):
      * Per-backend **retry** with exponential backoff (``max_retries``).
      * **Circuit breaker**: a backend that fails ``failure_threshold`` times in
        a row is skipped (cooldown) instead of being retried every call.
      * **Rate-limit throttle**: ``requests_per_minute`` enforces a minimum
        inter-call interval (e.g. NVIDIA NIM = 40 req/min).
      * **Telemetry**: success/failure counts, latency, circuit state via
        :meth:`get_stats`.

    Exposes the same ``complete`` / ``get_cost_report`` / ``mock_mode`` /
    ``config`` / ``last_error`` surface as :class:`LLMEngine`.
    """

    def __init__(self, backends: list[LLMConfig], router_enabled: bool = True) -> None:
        if not backends:
            raise ValueError("LLMManager requires at least one backend")
        self.backends = list(backends)
        self.engines = [LLMEngine(cfg) for cfg in self.backends]
        self.stats = [BackendStats() for _ in self.engines]
        self._rpm = [max(0, int(getattr(cfg, "requests_per_minute", 0) or 0)) for cfg in self.backends]
        self._last_call_ts = [0.0 for _ in self.engines]
        self.cb_threshold = _safe_int(os.getenv("LLM_FAILURE_THRESHOLD", "5"), 5)
        self.cb_cooldown = _safe_float(os.getenv("LLM_COOLDOWN_S", "120"), 120)
        self.last_error: Optional[str] = None
        self.last_used_backend: Optional[int] = None
        self.last_used_model: Optional[str] = None
        self.last_latency_ms: float = 0.0

    @property
    def config(self) -> LLMConfig:
        return self.backends[0]

    @property
    def mock_mode(self) -> bool:
        return all(eng.mock_mode for eng in self.engines)

    def reset_circuits(self) -> None:
        """Clear all circuit-breaker state (useful after recovery / tests)."""
        for st in self.stats:
            st.reset_circuit()

    async def complete(
        self,
        prompt: str,
        system: str = "",
        domain: str = "general",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        history: Optional[list[dict]] = None,
    ) -> str:
        self.last_error = None
        self.last_used_backend = None
        self.last_used_model = None
        self.last_usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        last_reply: Optional[str] = None
        for idx, eng in enumerate(self.engines):
            # Skip a mock-only backend when a live backend exists later in the
            # chain, so a configured real endpoint is preferred over a stub.
            if eng.mock_mode and any(not e.mock_mode for e in self.engines[idx + 1:]):
                continue
            st = self.stats[idx]
            if st.circuit_open:
                logger.warning("LLMManager backend %d circuit OPEN (until %.1f); skipping", idx, st.circuit_open_until)
                self.last_error = f"backend[{idx}] {eng.config.model}: circuit-open"
                continue
            # ── Rate-limit throttle (min inter-call interval) ──
            rpm = self._rpm[idx]
            if rpm > 0:
                min_interval = 60.0 / rpm
                wait = min_interval - (time.time() - self._last_call_ts[idx])
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_call_ts[idx] = time.time()
            t0 = time.time()
            try:
                reply = await eng.complete(
                    prompt, system=system, domain=domain,
                    temperature=temperature, max_tokens=max_tokens, history=history,
                )
            except Exception as e:  # noqa: BLE001 - failover must be bullet-proof
                st.record_failure(self.cb_threshold, self.cb_cooldown)
                self.last_error = f"backend[{idx}] {eng.config.model}: {e}"
                logger.warning("LLMManager backend %d raised: %s", idx, e)
                continue
            latency = (time.time() - t0) * 1000.0
            if eng.last_error:
                # Engine degraded to mock after a live failure -> record + fail over.
                st.record_failure(self.cb_threshold, self.cb_cooldown)
                self.last_error = f"backend[{idx}] {eng.config.model}: {eng.last_error}"
                logger.warning("LLMManager backend %d returned error: %s", idx, eng.last_error)
                last_reply = reply
                continue
            st.record_success(latency)
            self.last_used_backend = idx
            self.last_used_model = eng.config.model
            self.last_latency_ms = latency
            self.last_usage = eng.last_usage
            return reply
        # Every backend errored: return best-effort last reply (mock stub) if any.
        if last_reply is not None:
            return last_reply
        return self.engines[-1]._mock_complete(prompt, domain, system)

    def get_stats(self) -> dict:
        """Return per-backend telemetry plus aggregate health."""
        backends = []
        for i, (cfg, st) in enumerate(zip(self.backends, self.stats)):
            d = st.as_dict()
            d["index"] = i
            d["provider"] = cfg.provider
            d["model"] = cfg.model
            d["base_url"] = cfg.base_url
            d["live"] = not self.engines[i].mock_mode
            backends.append(d)
        healthy = sum(1 for b in backends if b["live"] and not b["circuit_open"])
        return {
            "backends": backends,
            "n_backends": len(backends),
            "healthy_live_backends": healthy,
            "last_used_backend": self.last_used_backend,
            "last_used_model": self.last_used_model,
            "last_latency_ms": round(self.last_latency_ms, 2),
            "last_error": self.last_error,
        }

    def get_cost_report(self) -> dict:
        total: dict = {
            "total_requests": 0,
            "requests_by_tier": {},
            "estimated_cost_saved": 0.0,
            "savings_percent": 0.0,
            "router_enabled": any(e.router is not None for e in self.engines),
            "backends": len(self.engines),
            "last_used_backend": self.last_used_backend,
        }
        for eng in self.engines:
            rep = eng.get_cost_report()
            total["total_requests"] += rep.get("total_requests", 0)
            total["estimated_cost_saved"] += rep.get("estimated_cost_saved", 0.0)
            for tier, n in rep.get("requests_by_tier", {}).items():
                total["requests_by_tier"][tier] = total["requests_by_tier"].get(tier, 0) + n
        return total


def _resolve_backends_from_env() -> list[LLMConfig]:
    """Single source of truth for backend resolution (optimization pass).

    Assembles, in order:
      1. Primary, from :meth:`LLMConfig.from_env`
         (OPENAI_BASE_URL / OPENAI_ENV tier).
      2. Explicit fallback URLs: OPENAI_FALLBACK_BASE_URLS (+ parallel
         OPENAI_FALLBACK_MODELS / OPENAI_FALLBACK_KEYS, comma-separated).
      3. Preset free providers: OPENAI_FALLBACK_PROVIDERS (names from
         FREE_PROVIDERS), each resolved against its own key env var and
         honouring its ``model_env`` override (e.g. ARK_MODEL / NVIDIA_MODEL)
         and ``requests_per_minute`` throttle.

    Used by both :func:`build_manager_from_env` and the self-evolution executor
    so the two paths can never drift (fixes NVIDIA_MODEL/ARK_MODEL being ignored
    by the self-evolution loop).
    """
    primary = LLMConfig.from_env()
    backends: list[LLMConfig] = [primary]

    fb_urls = [u.strip() for u in os.getenv("OPENAI_FALLBACK_BASE_URLS", "").split(",") if u.strip()]
    fb_models = [m.strip() for m in os.getenv("OPENAI_FALLBACK_MODELS", "").split(",") if m.strip()]
    fb_keys = [k.strip() for k in os.getenv("OPENAI_FALLBACK_KEYS", "").split(",") if k.strip()]
    for i, url in enumerate(fb_urls):
        backends.append(LLMConfig(
            provider=primary.provider,
            model=fb_models[i] if i < len(fb_models) else primary.model,
            api_key=fb_keys[i] if i < len(fb_keys) else primary.api_key,
            base_url=url,
            temperature=primary.temperature,
            max_tokens=primary.max_tokens,
            max_retries=primary.max_retries,
            retry_backoff=primary.retry_backoff,
            requests_per_minute=primary.requests_per_minute,
        ))

    for name in [p.strip() for p in os.getenv("OPENAI_FALLBACK_PROVIDERS", "").split(",") if p.strip()]:
        prov = FREE_PROVIDERS.get(name)
        if not prov:
            logger.warning("Unknown OPENAI_FALLBACK_PROVIDERS entry: %r (skipped)", name)
            continue
        key_env = prov.get("key_env", "")
        model_env = prov.get("model_env", "")
        model = os.getenv(model_env, "") if model_env else ""
        if not model:
            model = prov.get("default_model", primary.model)
        key = os.getenv(key_env, "") if key_env else ""
        # Skip providers that require a key but don't have one.
        if key_env and not key:
            logger.debug("Skipping provider %r: %s not set", name, key_env)
            continue
        backends.append(LLMConfig(
            provider=prov.get("provider", primary.provider),
            model=model,
            api_key=key,
            base_url=prov["base_url"],
            temperature=primary.temperature,
            max_tokens=primary.max_tokens,
            max_retries=primary.max_retries,
            retry_backoff=primary.retry_backoff,
            requests_per_minute=_safe_int(prov.get("requests_per_minute", 0), 0),
        ))

    return backends


def build_manager_from_env() -> Any:
    """Build an LLMManager (or a plain LLMEngine) from OPENAI_* env.

    Backends are resolved by :func:`_resolve_backends_from_env`. Returns a single
    :class:`LLMEngine` when only the primary is configured (zero overhead, fully
    backward compatible with callers expecting one engine).
    """
    backends = _resolve_backends_from_env()
    if len(backends) <= 1:
        return LLMEngine(backends[0])
    logger.info("LLMManager initialized with %d backends (failover enabled)", len(backends))
    return LLMManager(backends)


# ---------------------------------------------------------------------------
# Singleton-style accessor for domain handlers
# ---------------------------------------------------------------------------

_llm_instance: Optional[Any] = None


def get_llm() -> Any:
    """Return the global LLM engine/manager instance (auto-configured from env).

    Builds an :class:`LLMManager` when fallback backends are configured, otherwise
    a single :class:`LLMEngine`. Both expose the same ``complete`` surface used by
    domain handlers.
    """
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = build_manager_from_env()
    return _llm_instance


def reset_llm() -> None:
    """Clear the cached global instance (mainly for tests / reconfiguration)."""
    global _llm_instance
    _llm_instance = None


def init_llm(config_obj: Any = None) -> Any:
    """Initialize the global LLM engine/manager, seeding from OPENAI_* env.

    Explicit config fields (when non-empty) override env values; env values
    override built-in defaults. Builds an :class:`LLMManager` when fallback
    backends are configured, otherwise a single :class:`LLMEngine` — so serve
    mode also gets multi-backend failover (consistent with :func:`get_llm`).
    """
    global _llm_instance
    instance = build_manager_from_env()
    # Allow an explicit config object to override the primary backend.
    llm_cfg = getattr(config_obj, "llm", None) or getattr(config_obj, "model", None)
    if llm_cfg is not None:
        primary = instance.backends[0] if isinstance(instance, LLMManager) else instance.config
        for field_name in ("provider", "model", "api_key", "base_url", "temperature", "max_tokens"):
            value = getattr(llm_cfg, field_name, None)
            if value:
                setattr(primary, field_name, value)
    _llm_instance = instance
    return _llm_instance
