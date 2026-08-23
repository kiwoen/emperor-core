"""
清除 emperor.py 中两处"裸 pass 吞异常"技术债的回归测试。

主理人要求：改后 emperor.py 中不再存在 `except Exception: pass` 或
`except: pass` 这类静默吞异常；两处（成本快照、loop guard）应改为显式日志。

采用"读源码 grep"方式断言（与主理人给出的可选项一致）。
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EMPEROR_SRC = REPO_ROOT / "jarvis" / "emperor.py"


def _read() -> str:
    return EMPEROR_SRC.read_text(encoding="utf-8")


def test_emperor_no_silent_except():
    src = _read()
    # 不应再有裸吞异常
    assert not re.search(
        r"except\s+Exception\s*:\s*\n\s*pass\b", src
    ), "emperor.py 仍存在 `except Exception: pass` 裸吞异常"
    assert not re.search(
        r"except\s*:\s*\n\s*pass\b", src
    ), "emperor.py 仍存在 bare `except: pass` 裸吞异常"
    # 两处改后都应带显式日志（保留 exc_info=True，非静默）
    assert "成本快照不可用" in src
    assert "loop guard 检查失败" in src
    assert "exc_info=True" in src


def test_emperor_exceptions_now_logged_not_swallowed():
    """确认两处 except 改为 logger 调用，而非 `pass`。"""
    src = _read()
    # 成本快照块：except 之后是 logger.debug 而非 pass
    assert re.search(
        r'except Exception:\s*logger\.debug\(',
        src,
    ), "成本快照异常处理未改为 logger.debug"
    # loop guard 块：except 之后是 logger.warning 而非 pass
    assert re.search(
        r'except Exception:\s*logger\.warning\(',
        src,
    ), "loop guard 异常处理未改为 logger.warning"
