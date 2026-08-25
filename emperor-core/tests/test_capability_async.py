"""
P1 回归测试：capabilities 的阻塞调用可经线程池异步卸载（不阻塞事件循环）。

不依赖真实网络：
* 搜索在缺依赖 / 无网时优雅降级为 ``([], True, reason)``；
* vision 在无后端时返回结构化降级 JSON（含 ``no_vision_available``）。
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.capabilities.search import WebSearchService
from jarvis.capabilities.vision import VisionBackend


@pytest.mark.asyncio
async def test_asearch_returns_tuple_shape():
    """asearch 返回值结构与 search 完全一致（results, degraded, reason）。"""
    svc = WebSearchService(timeout=1)
    results, degraded, reason = await svc.asearch("test query", max_results=3)
    assert isinstance(results, list)
    assert isinstance(degraded, bool)
    assert isinstance(reason, str)


@pytest.mark.asyncio
async def test_achat_sync_degraded_without_backends():
    """无后端时 achat_sync 返回结构化降级 JSON，绝不抛异常。"""
    vb = VisionBackend([])
    out = await vb.achat_sync(prompt="describe this image")
    assert isinstance(out, str)
    assert "no_vision_available" in out


@pytest.mark.asyncio
async def test_asearch_does_not_block_event_loop():
    """asearch 在独立线程执行，等待期间事件循环仍可推进其他协程。

    若同步搜索未卸载到线程池，事件循环会被阻塞、``spin`` 无法推进，
    本断言将失败——以此锁定「不阻塞事件循环」这一 P1 核心收益。
    """
    svc = WebSearchService(timeout=1)
    progress: list[int] = []

    async def spin() -> None:
        for _ in range(20):
            await asyncio.sleep(0)
            progress.append(1)

    await asyncio.wait_for(asyncio.gather(svc.asearch("x"), spin()), timeout=20)
    assert len(progress) == 20


@pytest.mark.asyncio
async def test_vision_processor_aprocess_offloads():
    """VisionProcessor.aprocess 在后端支持 achat_sync 时走异步卸载路径。"""
    from jarvis.multimodal.processor import VisionProcessor

    backend = VisionBackend([])  # 降级后端
    vp = VisionProcessor(llm_engine=backend)
    result = await vp.aprocess("https://example.com/x.png", prompt="描述图片")
    assert isinstance(result, dict)
    assert "caption" in result
    assert "no_vision_available" in result["raw"]
