"""
genome_diff — 把「系统对自己基因做了什么改动」变成一份可审查的真实 diff。

自进化系统落地的最大陷阱之一：写回的是一句占位符（``"# cycle N evolved
genomes"``），而不是系统**真实改变的内容**。那样人类 reviewer 在 PR 里看不到
任何东西，闸门形同虚设——这正是调研中反覆出现的「零代码自修改」死亡之穴。

本模块让写回携带一份**真实的 unified diff**：
    - 进化前快照 ``before``（GenomeStore 风格 payload）
    - 进化后快照 ``after``
    - 二者 pretty-JSON 后的统一差异，文件路径指向仓库内的
      ``jarvis/court/genome_state.json``

这样 ``GitWriteChannel`` 应用的就是系统这一轮**真正改动的基因**，PR 里能看到
"A 大臣 temperature 0.9 → 0.62" 之类的具体变化——可审查、可回滚、可审计。

设计原则：
  1. 纯标准库（difflib），离线可用、零依赖；
  2. 文件不存在时生成「新增文件」diff（``--- /dev/null``），``git apply`` 可消化；
  3. 无变化则返回空串（调用方据此跳过写回，不制造空 PR）。
"""

from __future__ import annotations

import difflib
import json
from typing import Any, Dict

# 进化产物在仓库内的落点（相对仓库根）。GitWriteChannel 克隆后据此应用 patch。
GENOME_STATE_RELPATH = "jarvis/court/genome_state.json"


def _pretty(payload: Dict[str, Any]) -> list[str]:
    """把 payload 序列化为确定性（key 排序）的 JSON 行列表，供 diff 使用。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return text.splitlines(keepends=True)


def genome_state_diff(before: Dict[str, Any], after: Dict[str, Any],
                      relpath: str = GENOME_STATE_RELPATH) -> str:
    """生成 before→after 两份基因快照的统一差异（unified diff 文本）。

    Args:
        before: 进化前的 GenomeStore 风格 payload（可为空 dict = 文件尚不存在）。
        after:  进化后的 GenomeStore 风格 payload。
        relpath: 仓库内相对路径（diff 头部使用）。

    Returns:
        可 ``git apply`` 的 unified diff 文本；若 before == after 返回空串。
    """
    before_lines = _pretty(before) if before else []
    after_lines = _pretty(after)

    if before_lines == after_lines:
        return ""

    fromfile = "/dev/null" if not before else f"a/{relpath}"
    tofile = f"b/{relpath}"
    diff = difflib.unified_diff(
        before_lines, after_lines,
        fromfile=fromfile, tofile=tofile, lineterm="",
    )
    return "".join(diff)


def genome_state_file_content(payload: Dict[str, Any]) -> str:
    """把 payload 序列化为 ``genome_state.json`` 的文件内容（带尾换行）。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


__all__ = [
    "GENOME_STATE_RELPATH",
    "genome_state_diff",
    "genome_state_file_content",
]
