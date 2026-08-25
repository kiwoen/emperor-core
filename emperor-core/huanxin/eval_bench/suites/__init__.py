"""
huanxin.eval_bench.suites — 内置离线基准用例集。

当前提供：
    canonical — P0.6 黄金用例（math / code / retrieval / factual / refusal），
                零网络依赖，可在本机断言通过。
"""

from __future__ import annotations

from huanxin.eval_bench.suites.canonical import CanonicalSuite, build_canonical_suite

__all__ = ["CanonicalSuite", "build_canonical_suite"]
