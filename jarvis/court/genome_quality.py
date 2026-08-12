"""
genome_quality — 基因的「真实质量」函数（单一可复现来源）。

把原本散落在 :mod:`jarvis.self_evolve` 里的 :func:`true_quality` 抽成独立模块，
供**自进化安全闸门**与**编排引擎**共用同一个定义——避免「安全闸算的质量」和
「进化优化的质量」两套公式悄悄漂移（漂移本身就是一种 reward-hacking 漏洞）。

质量只依赖两个公开基因字段（温度、置信基线），夹在 [0.05, 0.97]，
保证任何基因都有非零成功率、且永远达不到完美（留上升空间）。
"""

from __future__ import annotations

# 基因最优区：进化要逼近的目标。温度≈0.4、置信基线≈0.9 时「真实质量」最高。
OPT_TEMPERATURE = 0.4
OPT_CONFIDENCE = 0.9

# 质量夹紧区间（任何基因都达不到满分，也绝不低于此地板）。
QUALITY_FLOOR = 0.05
QUALITY_CEIL = 0.97


def true_quality(genome: object) -> float:
    """基因的「真实质量」：离最优区越近越高（进化的优化目标）。

    ``genome`` 可以是 :class:`~jarvis.court.evolution.MinisterGenome` 对象，
    也可以是带 ``temperature`` / ``confidence_baseline`` 键的普通 dict。
    """
    temp = float(getattr(genome, "temperature", _as_dict(genome).get("temperature", 0.7)))
    conf = float(getattr(genome, "confidence_baseline", _as_dict(genome).get("confidence_baseline", 0.75)))
    q = 1.0 - 0.7 * abs(temp - OPT_TEMPERATURE) - 0.4 * abs(conf - OPT_CONFIDENCE)
    return max(QUALITY_FLOOR, min(QUALITY_CEIL, q))


def _as_dict(genome: object) -> dict:
    """dict 基因直接返回；对象基因取可读属性字典（容错）。"""
    if isinstance(genome, dict):
        return genome
    return {k: getattr(genome, k, None) for k in ("temperature", "confidence_baseline")}


__all__ = ["OPT_TEMPERATURE", "OPT_CONFIDENCE", "QUALITY_FLOOR", "QUALITY_CEIL", "true_quality"]
