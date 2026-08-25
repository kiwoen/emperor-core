"""能力服务包：文件上传 / 联网搜索 / 图文识别。

三个能力模块遵循「最小变更 + 结构化降级」原则：
* 内部绝不抛出 5xx；搜索 / 视觉在无 key 或网络异常时返回结构化降级结果。
* 上传校验失败以 ``ValueError`` 抛出，由路由层转成可读的 400 提示。
"""
from __future__ import annotations

from huanxin.capabilities.uploads import UploadStore
from huanxin.capabilities.search import WebSearchService
from huanxin.capabilities.vision import (
    VisionBackend,
    build_vision_processor,
    resolve_vision_backends,
)

__all__ = [
    "UploadStore",
    "WebSearchService",
    "VisionBackend",
    "build_vision_processor",
    "resolve_vision_backends",
]
