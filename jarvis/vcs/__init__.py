"""jarvis.vcs — 版本控制与安全写回通道。

目前包含 GitWriteChannel：自进化系统「写回自身代码」的唯一合法通道
（只开 PR，绝不直推 master/main）。
"""

from jarvis.vcs.git_channel import PROTECTED_BRANCHES, GitWriteChannel, ProposeResult

__all__ = ["PROTECTED_BRANCHES", "GitWriteChannel", "ProposeResult"]
