"""派发反偏置（UCB 探索 / 熵正则）测试。

覆盖：
  - ``_rank_ministers`` 的 UCB 探索项：被派发少的大臣获得更高探索权重 → 排名上升，
    对抗「成功者恒成功」的马太偏斜。
  - ``exploration_weight<=0`` / 无 ``dispatch_counts`` 时退化为纯历史成功率排序（零回归）。
  - ``SelfEvolutionEngine`` / ``SelfEvolveConfig`` 对 ``exploration_weight`` 的接入。
"""
import inspect

from jarvis.self_evolve import SelfEvolutionEngine, _rank_ministers
from jarvis.self_evolve_config import SelfEvolveConfig


def test_rank_ministers_fallback_no_exploration():
    """exploration_weight<=0 或缺少 dispatch_counts → 纯历史成功率降序（零回归）。"""
    group = ["math_alpha", "math_beta"]
    q = {("math_alpha", "math"): 0.4, ("math_beta", "math"): 0.9}
    # 默认参数（无 dispatch_counts）→ 退化分支
    assert _rank_ministers(group, "math", q) == ["math_beta", "math_alpha"]
    # 显式 exploration_weight=0 → 同样退化
    assert _rank_ministers(
        group, "math", q, dispatch_counts={}, exploration_weight=0.0
    ) == ["math_beta", "math_alpha"]


def test_rank_ministers_ucb_boosts_underdispatched():
    """开启探索后，被派发少（count 小）的大臣排名上升，对抗马太偏斜。"""
    group = ["a", "b"]
    # 历史成功率相同，但 a 已被派发很多次，b 很少
    q = {("a", "math"): 0.8, ("b", "math"): 0.8}
    counts = {("a", "math"): 100, ("b", "math"): 1}
    ranked = _rank_ministers(
        group, "math", q, dispatch_counts=counts, exploration_weight=0.5
    )
    # b 的探索项远大于 a → b 排前
    assert ranked[0] == "b"


def test_rank_ministers_ucb_zero_count_gets_max_boost():
    """从未被派发的大臣（count=0）获得最大探索项，应排在所有有历史者之前（同成功率下）。"""
    group = ["veteran", "rookie"]
    q = {("veteran", "math"): 0.9, ("rookie", "math"): 0.5}
    counts = {("veteran", "math"): 50, ("rookie", "math"): 0}
    ranked = _rank_ministers(
        group, "math", q, dispatch_counts=counts, exploration_weight=1.0
    )
    # rookie 探索项 = sqrt(ln(51)/1) ≈ 1.94，远大于 veteran 的 0.9 + 小探索项
    assert ranked[0] == "rookie"


def test_rank_ministers_exploration_monotonic():
    """探索权重越大，被冷落大臣的相对优势越明显（单调性 sanity check）。"""
    group = ["hot", "cold"]
    q = {("hot", "math"): 0.9, ("cold", "math"): 0.9}
    counts = {("hot", "math"): 200, ("cold", "math"): 1}
    low = _rank_ministers(group, "math", q, dispatch_counts=counts, exploration_weight=0.1)
    high = _rank_ministers(group, "math", q, dispatch_counts=counts, exploration_weight=2.0)
    # 两种权重下 cold 都应排前（差距足够大），验证探索项稳定生效
    assert low[0] == "cold"
    assert high[0] == "cold"


def test_engine_accepts_exploration_weight_param():
    """SelfEvolutionEngine.__init__ 接受 exploration_weight，默认 0.3。"""
    sig = inspect.signature(SelfEvolutionEngine.__init__)
    assert "exploration_weight" in sig.parameters
    assert sig.parameters["exploration_weight"].default == 0.3


def test_config_roundtrip_exploration_weight():
    """SelfEvolveConfig 默认与解析都带 exploration_weight。"""
    cfg = SelfEvolveConfig()
    assert cfg.exploration_weight == 0.3
    cfg2 = SelfEvolveConfig.from_dict({"exploration_weight": 0.0})
    assert cfg2.exploration_weight == 0.0
    cfg3 = SelfEvolveConfig.from_dict({})
    assert cfg3.exploration_weight == 0.3
