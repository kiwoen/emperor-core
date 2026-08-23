"""
``jarvis.async_core`` — async execution infrastructure.

Modules:
    - :mod:`jarvis.async_core.executor`    — :class:`AsyncExecutor` (priority + concurrency)
    - :mod:`jarvis.async_core.queue_manager` — :class:`QueueManager` (multi-priority producer-consumer)

Quick start::

    from jarvis.async_core import AsyncExecutor, QueueManager, Priority
"""

from jarvis.async_core.executor import AsyncExecutor, Priority, TaskHandle
from jarvis.async_core.queue_manager import QueueManager

__all__ = ["AsyncExecutor", "Priority", "TaskHandle", "QueueManager"]
