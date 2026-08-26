"""scripts/check_silent_except.py 单元测试。"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_silent_except as cse  # noqa: E402


def _write(tmp_path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_flags_broad_except_exception(tmp_path):
    f = _write(tmp_path, "bad.py", "try:\n    x()\nexcept Exception:\n    pass\n")
    v = cse.find_silent_broad_excepts(f)
    assert v and v[0][2] == "Exception"


def test_flags_bare_except(tmp_path):
    f = _write(tmp_path, "bad2.py", "try:\n    x()\nexcept:\n    pass\n")
    v = cse.find_silent_broad_excepts(f)
    assert v and v[0][2] == "bare"


def test_flags_tuple_containing_exception(tmp_path):
    f = _write(
        tmp_path, "bad3.py",
        "try:\n    x()\nexcept (ImportError, Exception):\n    pass\n",
    )
    assert cse.find_silent_broad_excepts(f)


def test_allows_narrow_types(tmp_path):
    f = _write(
        tmp_path, "ok.py",
        "try:\n    n = int(s)\nexcept (ValueError, IndexError):\n    pass\n",
    )
    assert cse.find_silent_broad_excepts(f) == []


def test_allows_logged_exception(tmp_path):
    f = _write(
        tmp_path, "ok2.py",
        "try:\n    x()\nexcept Exception:\n    logger.debug('x', exc_info=True)\n",
    )
    assert cse.find_silent_broad_excepts(f) == []


def test_allows_control_flow_noop(tmp_path):
    f = _write(
        tmp_path, "ok3.py",
        "try:\n    await ws()\nexcept WebSocketDisconnect:\n    pass\n",
    )
    assert cse.find_silent_broad_excepts(f) == []


def test_main_exit_codes(tmp_path, capsys):
    good = _write(tmp_path, "g.py", "try:\n    x()\nexcept ValueError:\n    pass\n")
    assert cse.main(["--paths", good]) == 0
    bad = _write(tmp_path, "b.py", "try:\n    x()\nexcept Exception:\n    pass\n")
    assert cse.main(["--paths", bad]) == 1


def test_real_core_scope_is_clean():
    """我们自建的自进化核心路径应当已经无静默吞异常。"""
    repo = os.path.join(os.path.dirname(__file__), "..")
    paths = [os.path.join(repo, p) for p in cse.DEFAULT_SCOPE]
    assert cse.scan_paths(paths) == []
