"""
GenomeInjector — 基因组 → LLM 参数注入

将 MinisterGenome 的可进化性状映射为实际 LLM 调用参数，
闭合「育种 → 进化 → 行为差异 → 功勋 → 选择」的核心价值回路。

设计原则：
  1. 确定性映射：相同 genome 总是产生相同 params（可复现）
  2. 合理区间约束：所有参数被钳制在安全的 API 范围内
  3. 渐进影响：每个 gene 的小变化对应 params 的平滑变化
  4. 提供者无关：映射逻辑不依赖具体 LLM provider

基因 → 参数映射表：
  - temperature          → GenerationParams.temperature
  - confidence_baseline  → 用于调整返回的 confidence 分数
  - exploration_rate     → top_p (高探索 → 高 top_p)
  - conservatism         → frequency_penalty / presence_penalty
  - specialization_weight → max_tokens 缩放因子
  - prompt_mutation_rate → 系统提示词变异概率（由 Minister 消费）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from jarvis.court.providers.base import GenerationParams


# ---------------------------------------------------------------------------
# 参数映射常量
# ---------------------------------------------------------------------------

class InjectionProfile:
    """基因 → LLM参数 的映射规则集合。

    每个字段描述了一个 gene 如何影响一个或多个 GenerationParams 字段。
    所有曲线使用线性插值，边界由 MIN/MAX 常量约束。
    """

    # --- temperature ---
    # 直接取自 genome.temperature（已在 [0.3, 1.0] 范围内由进化控制）
    TEMP_MIN = 0.1
    TEMP_MAX = 2.0

    # --- top_p (nucleus sampling) ---
    # 由 exploration_rate 驱动：高探索 = 大词汇池 = 高 top_p
    # exploration_rate ∈ [0, 1] → top_p ∈ [0.7, 1.0]
    TOP_P_MIN = 0.7
    TOP_P_MAX = 1.0

    # --- frequency_penalty ---
    # 由 conservatism 反驱动：保守 = 低 repetition penalty
    # conservatism ∈ [0, 1] → freq_penalty ∈ [-0.5, 0.5]
    FREQ_PENALTY_MIN = -0.5
    FREQ_PENALTY_MAX = 0.5

    # --- presence_penalty ---
    # 由 exploration_rate 驱动：探索 = 多谈新话题
    # exploration_rate ∈ [0, 1] → presence_penalty ∈ [-0.3, 0.6]
    PRESENCE_PENALTY_MIN = -0.3
    PRESENCE_PENALTY_MAX = 0.6

    # --- max_tokens ---
    # 由 specialization_weight 缩放：专精 = 更聚焦 = 可能更短
    # specialization_weight ∈ [0.5, 2.0] → 缩放因子 ∈ [0.5, 1.5]
    # 基础 max_tokens 由 Minister 传进来
    MAX_TOKENS_SCALE_MIN = 0.5
    MAX_TOKENS_SCALE_MAX = 1.5


# ---------------------------------------------------------------------------
# 注入结果
# ---------------------------------------------------------------------------


@dataclass
class InjectionResult:
    """GenomeInjector 的输出。

    包含已修改的 GenerationParams 以及从 genome 中提取的元数据，
    供 Minister 在调用后使用（如 confidence 调整）。
    """
    params: GenerationParams
    confidence_modifier: float       # 乘到原始 confidence 上的系数
    genome_temperature: float        # 记录原始 genome 温度（供日志）
    genome_exploration: float        # 记录原始 genome 探索率
    genome_conservatism: float       # 记录原始 genome 保守度
    genome_specialization: float     # 记录原始 genome 专精度
    prompt_mutation_active: bool     # 本轮是否触发系统提示词变异


# ---------------------------------------------------------------------------
# 核心注入器
# ---------------------------------------------------------------------------


class GenomeInjector:
    """将 MinisterGenome（或等价 dict）注入 GenerationParams 的桥梁。

    用法:
        injector = GenomeInjector()
        result = injector.inject(genome, base_params)

    genome 可以是 MinisterGenome 实例或 dict[str, float]，
    只要包含 temperature / confidence_baseline / exploration_rate /
    conservatism / specialization_weight / prompt_mutation_rate 字段。
    """

    def __init__(self, profile: Optional[InjectionProfile] = None) -> None:
        self.profile = profile or InjectionProfile()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def inject(
        self,
        genome: object,               # MinisterGenome 或 dict
        base_params: Optional[GenerationParams] = None,
        random_state: Optional[object] = None,  # random.Random 实例
    ) -> InjectionResult:
        """将 genome 映射到 GenerationParams。

        Args:
            genome: 基因源（MinisterGenome 实例或 dict）
            base_params: 基础参数模板（可为 None，使用默认值）
            random_state: 随机数生成器（用于 prompt_mutation 决策）

        Returns:
            InjectionResult 包含修改后的 params 和元数据
        """
        import random as _random
        from copy import deepcopy

        rng = random_state or _random
        # Deep-copy base_params to avoid mutating the caller's object
        params = deepcopy(base_params) if base_params is not None else GenerationParams()

        # 提取基因值
        g = self._extract_genes(genome)

        # 注入各项参数
        params.temperature = self._clamp(
            g["temperature"],
            self.profile.TEMP_MIN,
            self.profile.TEMP_MAX,
        )

        top_p = self._map(
            g["exploration_rate"], 0.0, 1.0,
            self.profile.TOP_P_MIN, self.profile.TOP_P_MAX,
        )
        params.extra["top_p"] = round(top_p, 4)

        freq_penalty = self._map(
            1.0 - g["conservatism"], 0.0, 1.0,       # 反转：保守=低惩罚
            self.profile.FREQ_PENALTY_MIN, self.profile.FREQ_PENALTY_MAX,
        )
        params.extra["frequency_penalty"] = round(freq_penalty, 4)

        presence_penalty = self._map(
            g["exploration_rate"], 0.0, 1.0,
            self.profile.PRESENCE_PENALTY_MIN, self.profile.PRESENCE_PENALTY_MAX,
        )
        params.extra["presence_penalty"] = round(presence_penalty, 4)

        # max_tokens 缩放
        token_scale = self._map(
            g["specialization_weight"], 0.5, 2.0,
            self.profile.MAX_TOKENS_SCALE_MIN, self.profile.MAX_TOKENS_SCALE_MAX,
        )
        params.max_tokens = max(1, int(params.max_tokens * token_scale))

        # confidence 修正：confidence_baseline 作为调用后乘数
        confidence_modifier = float(g["confidence_baseline"])

        # prompt mutation 决策（由 Minister 消费，这里只做判定）
        prompt_mutation_active = rng.random() < g["prompt_mutation_rate"]

        return InjectionResult(
            params=params,
            confidence_modifier=confidence_modifier,
            genome_temperature=float(g["temperature"]),
            genome_exploration=float(g["exploration_rate"]),
            genome_conservatism=float(g["conservatism"]),
            genome_specialization=float(g["specialization_weight"]),
            prompt_mutation_active=prompt_mutation_active,
        )

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_genes(genome: object) -> dict[str, float]:
        """从 MinisterGenome 或 dict 中提取基因值。

        返回包含所有必需 keys 的 dict，缺失值使用默认值。
        """
        if isinstance(genome, dict):
            return {
                "temperature": float(genome.get("temperature", 0.7)),
                "confidence_baseline": float(genome.get("confidence_baseline", 0.85)),
                "exploration_rate": float(genome.get("exploration_rate", 0.3)),
                "conservatism": float(genome.get("conservatism", 0.5)),
                "specialization_weight": float(genome.get("specialization_weight", 1.0)),
                "prompt_mutation_rate": float(genome.get("prompt_mutation_rate", 0.1)),
            }

        # dataclass 实例
        return {
            "temperature": float(getattr(genome, "temperature", 0.7)),
            "confidence_baseline": float(getattr(genome, "confidence_baseline", 0.85)),
            "exploration_rate": float(getattr(genome, "exploration_rate", 0.3)),
            "conservatism": float(getattr(genome, "conservatism", 0.5)),
            "specialization_weight": float(getattr(genome, "specialization_weight", 1.0)),
            "prompt_mutation_rate": float(getattr(genome, "prompt_mutation_rate", 0.1)),
        }

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        """将值钳制到 [low, high] 区间。"""
        return max(low, min(high, float(value)))

    @staticmethod
    def _map(
        value: float,
        in_low: float, in_high: float,
        out_low: float, out_high: float,
    ) -> float:
        """线性映射：把 value 从 [in_low, in_high] 映射到 [out_low, out_high]。

        输入超出范围时自动钳制。
        """
        clamped = max(in_low, min(in_high, float(value)))
        if in_high == in_low:
            return (out_low + out_high) / 2.0
        ratio = (clamped - in_low) / (in_high - in_low)
        return out_low + ratio * (out_high - out_low)
