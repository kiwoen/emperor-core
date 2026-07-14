"""
测试 GenomeInjector — 基因组 → LLM 参数注入

覆盖：
  1. 参数映射正确性（temperature / top_p / frequency_penalty / presence_penalty / max_tokens）
  2. 边界值钳制（极值输入 → 安全输出）
  3. confidence_modifier 传递
  4. prompt_mutation_active 判定（确定性 + 概率）
  5. dict 和 dataclass 两种 genome 源兼容
  6. Minister._try_real_model 注入流程（集成测试）
  7. 缺失 genome / injector 时的降级（不注入）
"""

import pytest

from jarvis.court.genome_injector import (
    GenomeInjector,
    InjectionProfile,
    InjectionResult,
)
from jarvis.court.providers.base import GenerationParams
from jarvis.court.minister import Minister, MinisterProfile


# ---------------------------------------------------------------------------
# 1. 参数映射正确性
# ---------------------------------------------------------------------------

class TestGenomeInjectorMapping:
    """测试基因值到 GenerationParams 的映射规则。"""

    def test_temperature_direct_mapping(self):
        """temperature 直接透传（钳制到 [0.1, 2.0]）。"""
        injector = GenomeInjector()
        genome = {
            "temperature": 0.85,
            "confidence_baseline": 0.9,
            "exploration_rate": 0.3,
            "conservatism": 0.5,
            "specialization_weight": 1.0,
            "prompt_mutation_rate": 0.1,
        }
        result = injector.inject(genome)
        assert result.params.temperature == 0.85

    def test_top_p_from_exploration(self):
        """top_p 由 exploration_rate 驱动：0→0.7, 1→1.0。"""
        injector = GenomeInjector()

        # 低探索 → 低 top_p
        low_genome = dict(temperature=0.7, confidence_baseline=0.85,
                          exploration_rate=0.0, conservatism=0.5,
                          specialization_weight=1.0, prompt_mutation_rate=0.1)
        low_result = injector.inject(low_genome)
        assert 0.69 <= low_result.params.extra["top_p"] <= 0.71

        # 高探索 → 高 top_p
        high_genome = dict(temperature=0.7, confidence_baseline=0.85,
                           exploration_rate=1.0, conservatism=0.5,
                           specialization_weight=1.0, prompt_mutation_rate=0.1)
        high_result = injector.inject(high_genome)
        assert 0.99 <= high_result.params.extra["top_p"] <= 1.01

        # 中探索 → 中间 top_p
        mid_genome = dict(temperature=0.7, confidence_baseline=0.85,
                          exploration_rate=0.5, conservatism=0.5,
                          specialization_weight=1.0, prompt_mutation_rate=0.1)
        mid_result = injector.inject(mid_genome)
        assert 0.84 <= mid_result.params.extra["top_p"] <= 0.86

    def test_frequency_penalty_from_conservatism(self):
        """frequency_penalty 由 conservatism 反驱动。"""
        injector = GenomeInjector()

        # 高保守 → 低频率惩罚
        conservative = dict(temperature=0.7, confidence_baseline=0.85,
                            exploration_rate=0.3, conservatism=1.0,
                            specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(conservative)
        assert result.params.extra["frequency_penalty"] <= -0.45

        # 低保守 → 高频率惩罚
        explorer = dict(temperature=0.7, confidence_baseline=0.85,
                        exploration_rate=0.3, conservatism=0.0,
                        specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(explorer)
        assert result.params.extra["frequency_penalty"] >= 0.45

    def test_presence_penalty_from_exploration(self):
        """presence_penalty 由 exploration_rate 驱动。"""
        injector = GenomeInjector()

        low = dict(temperature=0.7, confidence_baseline=0.85,
                   exploration_rate=0.0, conservatism=0.5,
                   specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(low)
        assert -0.31 <= result.params.extra["presence_penalty"] <= -0.29

        high = dict(temperature=0.7, confidence_baseline=0.85,
                    exploration_rate=1.0, conservatism=0.5,
                    specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(high)
        assert 0.59 <= result.params.extra["presence_penalty"] <= 0.61

    def test_max_tokens_from_specialization(self):
        """max_tokens 由 specialization_weight 缩放。"""
        injector = GenomeInjector()
        base = GenerationParams(max_tokens=2000)

        # 专精 → 更短
        focused = dict(temperature=0.7, confidence_baseline=0.85,
                       exploration_rate=0.3, conservatism=0.5,
                       specialization_weight=0.5, prompt_mutation_rate=0.1)
        result = injector.inject(focused, base_params=base)
        assert result.params.max_tokens == 1000  # 2000 * 0.5

        # 泛化 → 更长
        broad = dict(temperature=0.7, confidence_baseline=0.85,
                     exploration_rate=0.3, conservatism=0.5,
                     specialization_weight=2.0, prompt_mutation_rate=0.1)
        result = injector.inject(broad, base_params=base)
        assert result.params.max_tokens == 3000  # 2000 * 1.5


# ---------------------------------------------------------------------------
# 2. 边界值钳制
# ---------------------------------------------------------------------------

class TestGenomeInjectorClamping:
    """测试极值输入被钳制到安全范围。"""

    def test_temperature_clamped_min(self):
        injector = GenomeInjector()
        genome = dict(temperature=-0.5, confidence_baseline=0.85,
                      exploration_rate=0.3, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(genome)
        assert result.params.temperature == 0.1  # clamped to TEMP_MIN

    def test_temperature_clamped_max(self):
        injector = GenomeInjector()
        genome = dict(temperature=5.0, confidence_baseline=0.85,
                      exploration_rate=0.3, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(genome)
        assert result.params.temperature == 2.0  # clamped to TEMP_MAX

    def test_exploration_clamped(self):
        """exploration_rate 超出 [0,1] 时 top_p 仍在安全范围。"""
        injector = GenomeInjector()
        genome = dict(temperature=0.7, confidence_baseline=0.85,
                      exploration_rate=999, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(genome)
        top_p = result.params.extra["top_p"]
        assert 0.7 <= top_p <= 1.0


# ---------------------------------------------------------------------------
# 3. confidence_modifier 传递
# ---------------------------------------------------------------------------

class TestGenomeInjectorConfidence:
    """测试 confidence_baseline 作为 post-call 乘数传递。"""

    def test_confidence_modifier_high(self):
        injector = GenomeInjector()
        genome = dict(temperature=0.7, confidence_baseline=0.95,
                      exploration_rate=0.3, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(genome)
        assert result.confidence_modifier == 0.95

    def test_confidence_modifier_low(self):
        injector = GenomeInjector()
        genome = dict(temperature=0.7, confidence_baseline=0.3,
                      exploration_rate=0.3, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(genome)
        assert result.confidence_modifier == 0.3


# ---------------------------------------------------------------------------
# 4. prompt_mutation_active 判定
# ---------------------------------------------------------------------------

class TestGenomeInjectorPromptMutation:
    """测试 prompt_mutation_rate → prompt_mutation_active 概率判定。"""

    def test_mutation_active_when_rate_is_1(self):
        """prompt_mutation_rate=1.0 → 总是触发变异。"""
        import random
        injector = GenomeInjector()
        genome = dict(temperature=0.7, confidence_baseline=0.85,
                      exploration_rate=0.3, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=1.0)
        rng = random.Random(42)
        for _ in range(20):
            result = injector.inject(genome, random_state=rng)
            assert result.prompt_mutation_active is True

    def test_mutation_inactive_when_rate_is_0(self):
        """prompt_mutation_rate=0.0 → 从不触发变异。"""
        import random
        injector = GenomeInjector()
        genome = dict(temperature=0.7, confidence_baseline=0.85,
                      exploration_rate=0.3, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=0.0)
        rng = random.Random(42)
        for _ in range(20):
            result = injector.inject(genome, random_state=rng)
            assert result.prompt_mutation_active is False

    def test_mutation_rate_50_percent_statistical(self):
        """prompt_mutation_rate=0.5 → 约 50% 触发。"""
        import random
        injector = GenomeInjector()
        genome = dict(temperature=0.7, confidence_baseline=0.85,
                      exploration_rate=0.3, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=0.5)
        rng = random.Random(42)
        active_count = sum(
            1 for _ in range(100)
            if injector.inject(genome, random_state=rng).prompt_mutation_active
        )
        assert 35 <= active_count <= 65  # 合理范围（宽松容忍）


# ---------------------------------------------------------------------------
# 5. 基因组来源兼容性
# ---------------------------------------------------------------------------

class TestGenomeInjectorSources:
    """测试 dict 和 dataclass 两种 genome 源的兼容性。"""

    def test_dict_source(self):
        injector = GenomeInjector()
        genome = {
            "temperature": 0.5,
            "confidence_baseline": 0.8,
            "exploration_rate": 0.6,
            "conservatism": 0.3,
            "specialization_weight": 1.2,
            "prompt_mutation_rate": 0.05,
        }
        result = injector.inject(genome)
        assert result.params.temperature == 0.5
        assert result.confidence_modifier == 0.8

    def test_dataclass_source(self):
        from dataclasses import dataclass

        @dataclass
        class FakeGenome:
            temperature: float = 0.5
            confidence_baseline: float = 0.8
            exploration_rate: float = 0.6
            conservatism: float = 0.3
            specialization_weight: float = 1.2
            prompt_mutation_rate: float = 0.05

        injector = GenomeInjector()
        genome = FakeGenome()
        result = injector.inject(genome)
        assert result.params.temperature == 0.5
        assert result.confidence_modifier == 0.8

    def test_missing_keys_default(self):
        """缺失基因键时使用默认值。"""
        injector = GenomeInjector()
        genome = {}  # 空 genome
        result = injector.inject(genome)
        assert result.params.temperature == 0.7
        assert result.confidence_modifier == 0.85
        assert result.genome_exploration == 0.3
        assert result.genome_conservatism == 0.5


# ---------------------------------------------------------------------------
# 6. 元数据传递
# ---------------------------------------------------------------------------

class TestGenomeInjectorMetadata:
    """测试 InjectionResult 中的元数据字段。"""

    def test_metadata_fields_present(self):
        injector = GenomeInjector()
        genome = dict(temperature=0.6, confidence_baseline=0.75,
                      exploration_rate=0.4, conservatism=0.6,
                      specialization_weight=0.9, prompt_mutation_rate=0.2)
        result = injector.inject(genome)
        assert result.genome_temperature == 0.6
        assert result.genome_exploration == 0.4
        assert result.genome_conservatism == 0.6
        assert result.genome_specialization == 0.9
        assert isinstance(result.prompt_mutation_active, bool)


# ---------------------------------------------------------------------------
# 7. 自定义 InjectionProfile
# ---------------------------------------------------------------------------

class TestCustomInjectionProfile:
    """测试自定义 InjectionProfile 的映射行为。"""

    def test_custom_profile_changes_top_p(self):
        profile = InjectionProfile()
        profile.TOP_P_MIN = 0.5
        profile.TOP_P_MAX = 0.9

        injector = GenomeInjector(profile=profile)
        genome = dict(temperature=0.7, confidence_baseline=0.85,
                      exploration_rate=0.0, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=0.1)
        result = injector.inject(genome)
        # exploration=0 → top_p should be near TOP_P_MIN=0.5
        assert 0.49 <= result.params.extra["top_p"] <= 0.51


# ---------------------------------------------------------------------------
# 8. Minister 集成测试
# ---------------------------------------------------------------------------

class TestMinisterGenomeIntegration:
    """测试 Minister._try_real_model 中的基因组注入流程。"""

    @pytest.mark.asyncio
    async def test_minister_uses_genome_params(self):
        """设置 genome 后，_try_real_model 使用基因组参数。"""
        import asyncio

        # 创建一个假的 provider 来捕获参数
        class FakeProvider:
            is_available = True
            _captured_params = None
            _captured_prompt = None

            async def generate(self, prompt, params):
                FakeProvider._captured_params = params
                FakeProvider._captured_prompt = prompt
                from jarvis.court.providers.base import ModelResponse
                return ModelResponse(
                    text="ok",
                    model="test-model",
                    confidence=0.85,
                )

        profile = MinisterProfile(
            title="测试官",
            archetype="GPT-4",
            domain="testing",
            strengths=["test"],
            weaknesses=["none"],
        )
        minister = Minister(profile)
        minister.set_provider(FakeProvider())

        injector = GenomeInjector()
        minister.set_genome_injector(injector)

        genome = dict(
            temperature=0.42,
            confidence_baseline=0.88,
            exploration_rate=0.75,
            conservatism=0.25,
            specialization_weight=1.5,
            prompt_mutation_rate=0.0,
        )
        minister.set_genome(genome)

        from jarvis.court.minister import Edict
        edict = Edict(edict_id="test-1", intent="hello world")

        result = await minister._try_real_model(edict)

        assert result is not None
        output, confidence = result
        assert output == "ok"

        # 验证注入参数
        params = FakeProvider._captured_params
        assert params.temperature == 0.42
        assert "top_p" in params.extra
        assert "frequency_penalty" in params.extra
        assert "presence_penalty" in params.extra

        # 验证 confidence 被修饰
        # confidence_modifier = 0.88, raw=0.85 → 0.85*0.88 = 0.748
        assert 0.74 <= confidence <= 0.75

    @pytest.mark.asyncio
    async def test_minister_falls_back_gracefully_without_genome(self):
        """无 genome 时 _try_real_model 正常使用默认参数。"""
        class FakeProvider:
            is_available = True

            async def generate(self, prompt, params):
                from jarvis.court.providers.base import ModelResponse
                return ModelResponse(
                    text="ok", model="test", confidence=0.85,
                )

        profile = MinisterProfile(
            title="默认官",
            archetype="Claude",
            domain="default",
            strengths=["x"],
            weaknesses=["y"],
        )
        minister = Minister(profile)
        minister.set_provider(FakeProvider())

        from jarvis.court.minister import Edict
        edict = Edict(edict_id="test-2", intent="test")

        result = await minister._try_real_model(edict)
        assert result is not None
        output, confidence = result
        assert output == "ok"
        assert confidence == 0.85  # 无修饰

    @pytest.mark.asyncio
    async def test_minister_skips_injection_without_injector(self):
        """genome 存在但 injector 缺失 → 跳过注入，不影响调用。"""
        class FakeProvider:
            is_available = True
            _captured_temp = None

            async def generate(self, prompt, params):
                FakeProvider._captured_temp = params.temperature
                from jarvis.court.providers.base import ModelResponse
                return ModelResponse(
                    text="ok", model="test", confidence=0.85,
                )

        profile = MinisterProfile(
            title="无注入器官",
            archetype="Claude",
            domain="default",
            strengths=["x"],
            weaknesses=["y"],
        )
        minister = Minister(profile, system_prompt_template="")
        minister.set_provider(FakeProvider())

        # 设置 genome 但不设置 injector
        genome = dict(temperature=0.1, confidence_baseline=0.5,
                      exploration_rate=0.3, conservatism=0.5,
                      specialization_weight=1.0, prompt_mutation_rate=0.1)
        minister.set_genome(genome)

        from jarvis.court.minister import Edict
        edict = Edict(edict_id="test-3", intent="test")

        result = await minister._try_real_model(edict)
        assert result is not None

        # 没有 injector，温度应该是 minister 默认的 0.7 而非 genome 的 0.1
        assert FakeProvider._captured_temp == 0.7

    @pytest.mark.asyncio
    async def test_receive_edict_with_genome_injection(self):
        """完整路径：receive_edict → _try_real_model（有 genome）→ Memorial。"""
        class FakeProvider:
            is_available = True

            async def generate(self, prompt, params):
                from jarvis.court.providers.base import ModelResponse
                return ModelResponse(
                    text="genome-driven response",
                    model="test",
                    confidence=0.80,
                )

        profile = MinisterProfile(
            title="基因组大臣",
            archetype="GPT-4",
            domain="writing",
            strengths=["write"],
            weaknesses=["math"],
            quality_score=0.9,
        )
        minister = Minister(profile, system_prompt_template="You are {title}.")
        minister.set_provider(FakeProvider())
        minister.set_genome_injector(GenomeInjector())

        genome = dict(temperature=0.55, confidence_baseline=0.78,
                      exploration_rate=0.6, conservatism=0.4,
                      specialization_weight=1.1, prompt_mutation_rate=0.0)
        minister.set_genome(genome)

        from jarvis.court.minister import Edict
        edict = Edict(edict_id="full-1", intent="write a poem about AI")

        memorial = await minister.receive_edict(edict)
        assert memorial.success is True
        assert "genome-driven response" in memorial.output
        # confidence: 0.80 * 0.78 ≈ 0.624
        assert 0.60 <= memorial.confidence <= 0.64
