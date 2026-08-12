#!/usr/bin/env python3
"""
P2.2 CI 反向校验脚本：拦截「绕过写回通道直推受保护分支」的非法代码。

自进化系统唯一合法的代码写回路径是 ``jarvis/vcs/git_channel.py``
（GitWriteChannel，只开 PR 绝不直推 master）。本脚本在 CI 中对仓库源码做
静态扫描，一旦发现**其他任何文件**出现以下模式，立即判红（exit 1）：

  1. ``git push`` 且目标分支为 master / main；
  2. Python ``repo.git.push(...)`` / ``.push(...)`` 且涉及 master / main；
  3. ``gh api`` 使用 ``--method PUT|POST`` 且路径命中 master / main
     （例如改分支保护、直推受保护 ref）；但 ``.../pulls`` 创建 PR 属合法，放行。

合法例外：``jarvis/vcs/git_channel.py`` 本身（它是受控的写回通道），
允许出现 ``push`` / ``gh api`` 调用——因为它的 push 目标永远是新建的
absorb 分支，且内部有 ``_assert_no_protected_push`` 兜底。

Usage::
    python scripts/check_write_protect.py            # 扫描 jarvis/
    python scripts/check_write_protect.py --root .    # 指定根目录
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List

# 受保护分支
PROTECTED = ("master", "main")

# 合法例外文件（唯一受控写回通道）。用后缀匹配以兼容不同的扫描根目录。
ALLOWED_FILES = ("vcs/git_channel.py", "jarvis/vcs/git_channel.py")


def _has_protected(line: str) -> bool:
    low = line.lower()
    return any(re.search(r"\b" + b + r"\b", low) for b in PROTECTED)


def _line_is_violation(line: str) -> bool:
    """判断单行是否命中违规模式（排除合法的 PR 创建）。

    采用 token 级检测，兼容 ``["git","push","origin","master"]`` 列表写法、
    ``repo.git.push("master")``、``gh api .../branches/master/protection -X PUT``。
    """
    low = line.lower()
    if not _has_protected(low):
        return False

    # 模式 1：git push 到受保护分支（git 与 push 同现即可，容忍列表/字符串写法）
    if re.search(r"\bgit\b", low) and re.search(r"\bpush\b", low):
        return True

    # 模式 2：Python .push(...) 涉及受保护分支
    if re.search(r"\.push\(", low):
        return True

    # 模式 3：gh api 改动受保护分支（但 .../pulls 创建 PR 属合法，放行）
    if re.search(r"\bgh\b", low) and "api" in low and "/pulls" not in low:
        return True

    return False


def scan_file(path: str) -> List[str]:
    """扫描单个文件，返回违规模式描述列表。"""
    violations: List[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if _line_is_violation(line):
                    violations.append(f"{path}:{lineno}: {line.rstrip()}")
    except OSError as exc:
        print(f"[check_write_protect] 跳过无法读取的文件 {path}: {exc}", file=sys.stderr)
    return violations


def scan_paths(root: str, allowed: Tuple[str, ...] = ALLOWED_FILES) -> List[str]:
    """扫描 root 下所有 .py 文件（递归），返回全部违规行。"""
    all_violations: List[str] = []
    for dirpath, _dirs, files in os.walk(root):
        # 跳过非源码与常见无关目录
        if any(seg in dirpath for seg in ("/.git", "/__pycache__", "/.venv", "/venv")):
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if any(rel == a or rel.endswith(a) for a in allowed):
                continue
            all_violations.extend(scan_file(full))
    return all_violations


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI 写保护反向校验")
    parser.add_argument(
        "--root", default=None,
        help="扫描根目录（默认：脚本上级目录，即仓库根）",
    )
    parser.add_argument(
        "--fail-on-violation", action="store_true", default=True,
        help="发现违规时 exit 1（默认开启）",
    )
    args = parser.parse_args(argv)

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 只扫 jarvis/ 下的源码（self-evolving 后端）
    jarvis_root = os.path.join(root, "jarvis")
    scan_target = jarvis_root if os.path.isdir(jarvis_root) else root

    violations = scan_paths(scan_target)
    if violations:
        print("WRITE-PROTECT VIOLATION: 检测到绕过写回通道直推受保护分支的代码")
        for v in violations:
            print("  -", v)
        print(
            "\n唯一合法的代码写回路径是 jarvis/vcs/git_channel.py "
            "(GitWriteChannel, 只开 PR 绝不直推 master)。"
        )
        return 1
    print("[check_write_protect] OK: 未发现非法直推受保护分支的写回代码")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
