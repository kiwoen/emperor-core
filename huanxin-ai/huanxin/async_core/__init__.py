"""
``huanxin.async_core`` — async execution infrastructure.

Modules:
    - :mod:`huanxin.async_core.executor`    — :class:`AsyncExecutor` (priority + concurrency)
    - :mod:`huanxin.async_core.queue_manager` — :class:`QueueManager` (multi-priority producer-consumer)

Quick start::

    from huanxin.async_core import AsyncExecutor, QueueManager, Priority
"""

from huanxin.async_core.executor import AsyncExecutor, Priority, TaskHandle
from huanxin.async_core.queue_manager import QueueManager

__all__ = ["AsyncExecutor", "Priority", "TaskHandle", "QueueManager"]
