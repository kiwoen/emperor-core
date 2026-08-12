#!/usr/bin/env python3
"""CI 反向校验：自进化核心路径禁止「静默吞异常」。

研究结论（自进化系统头号失败模式 = silent failure）：核心进化路径里
``except Exception: pass`` / ``except: pass`` 会让错误无声消失，
系统在你不知情的情况下持续劣化。本脚本用 AST 扫描核心路径，发现
「宽泛异常 + 函数体只有 pass」的静默吞异常即判红。

判定规则（刻意简单、可审计）：
  - 命中：``except:``（裸）/ ``except Exception`` / ``except BaseException``
    / ``except (..., Exception)``，且 handler 函数体只有一条 ``pass``；
  - 放行：只捕获**具体窄类型**的 ``except (ValueError, IndexError): pass``
    这类「解析即跳过」惯用法，以及 WebSocketDisconnect / CancelledError /
    QueueFull / QueueEmpty / GeneratorExit / ImportError 等控制流 no-op
    ——它们不含 "Exception" 子串，天然不命中。

默认只约束我们自建的自进化核心（court / vcs / eval_bench / guardrail /
router），不强改上游 emperor-core 其余模块的既有写法。可用 ``--paths``
自定义扫描范围。

用法::

    python scripts/check_silent_except.py                 # 扫描默认核心路径
    python scripts/check_silent_except.py --paths jarvis/  # 自定义范围
退出码：发现静默吞异常 → 1；否则 → 0。
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from typing import Iterable, List, Tuple

# 默认约束范围：我们自建的自进化核心路径
DEFAULT_SCOPE: Tuple[str, ...] = (
    "jarvis/court",
    "jarvis/vcs",
    "jarvis/eval_bench",
    "jarvis/guardrail_chain.py",
    "jarvis/model_router.py",
    "jarvis/emperor.py",
)

Violation = Tuple[str, int, str]


def _is_broad(exc_text: str) -> bool:
    """宽泛异常判定：裸 except 或文本含 Exception / BaseException。"""
    return exc_text == "bare" or "Exception" in exc_text


def _iter_py_files(path: str) -> Iterable[str]:
    if os.path.isfile(path):
        if path.endswith(".py"):
            yield path
        return
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(root, name)


def find_silent_broad_excepts(path: str) -> List[Violation]:
    """返回 *path*（文件或目录）下所有「宽泛静默吞异常」的位置。"""
    out: List[Violation] = []
    for pyfile in _iter_py_files(path):
        try:
            src = open(pyfile, encoding="utf-8").read()
            tree = ast.parse(src, filename=pyfile)
        except (SyntaxError, UnicodeDecodeError):
            continue  # 解析失败的文件交给编译/lint 处理，不在本闸职责内
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                exc_text = ast.unparse(node.type) if node.type is not None else "bare"
                if _is_broad(exc_text):
                    rel = pyfile.replace(os.sep, "/")
                    out.append((rel, node.lineno, exc_text))
    return out


def scan_paths(paths: Iterable[str]) -> List[Violation]:
    violations: List[Violation] = []
    for p in paths:
        if os.path.exists(p):
            violations.extend(find_silent_broad_excepts(p))
    return violations


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="自进化核心路径静默吞异常检查")
    ap.add_argument(
        "--paths", nargs="+", default=list(DEFAULT_SCOPE),
        help="要扫描的文件/目录（默认：自进化核心路径）",
    )
    args = ap.parse_args(argv)

    violations = scan_paths(args.paths)
    if violations:
        print("❌ 检测到「宽泛静默吞异常」（自进化头号失败模式 = silent failure）：")
        for f, lineno, exc in violations:
            print(f"  {f}:{lineno}  except {exc}: pass")
        print(
            "\n请改为 logger.debug/warning(..., exc_info=True) 使其可观测，"
            "或改用具体的窄异常类型。"
        )
        return 1
    print("✅ 自进化核心路径无静默吞异常（所有异常均可观测）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
