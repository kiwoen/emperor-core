"""jarvis.vcs — 版本控制与安全写回通道。

- :class:`GitWriteChannel`：自进化系统「写回自身代码」的唯一合法通道
  （只开 PR，绝不直推 master/main）。
- :class:`WritebackGate`：写回前评测闸——基准评测不过就绝不写回
  （DGM 闭环的「基准评测」一环）。
"""

from jarvis.vcs.git_channel import PROTECTED_BRANCHES, GitWriteChannel, ProposeResult
from jarvis.vcs.writeback_gate import GateDecision, WritebackBlocked, WritebackGate

__all__ = [
    "PROTECTED_BRANCHES",
    "GitWriteChannel",
    "ProposeResult",
    "GateDecision",
    "WritebackBlocked",
    "WritebackGate",
]
