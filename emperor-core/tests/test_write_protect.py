"""P2.2 check_write_protect 脚本单元测试。

验证：
- 非法 ``git push master`` / ``gh api --method PUT ... master`` / ``.push("master")`` 被识别；
- 合法 ``gh api .../pulls``（PR 创建）不误报；
- ``jarvis/vcs/git_channel.py`` 作为例外不被扫描；
- main() 在违规时返回 1、无违规时返回 0。
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

# 让脚本可被 import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import check_write_protect as cwp  # noqa: E402


def _write_tmp(tree: dict[str, str]) -> str:
    root = tempfile.mkdtemp(prefix="wp-")
    for rel, content in tree.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
    return root


def test_detects_git_push_master():
    root = _write_tmp({"jarvis/evil.py": 'subprocess.run(["git","push","origin","master"])\n'})
    v = cwp.scan_paths(os.path.join(root, "jarvis"))
    assert v
    assert any("git" in line and "push" in line for line in v)


def test_detects_gh_api_put_master():
    root = _write_tmp({
        "jarvis/evil.py": 'os.system("gh api repos/x/branches/master/protection -X PUT")\n',
    })
    v = cwp.scan_paths(os.path.join(root, "jarvis"))
    assert v
    assert any("gh api" in line for line in v)


def test_ignores_legal_pr_creation():
    root = _write_tmp({
        "jarvis/ok.py": 'subprocess.run(["gh","api","repos/x/pulls","-f","base=master"])\n',
    })
    v = cwp.scan_paths(os.path.join(root, "jarvis"))
    assert not v, f"误报合法 PR 创建: {v}"


def test_ignores_git_channel_exception():
    content = 'self._run(["git","-C",w,"push","origin",branch])\n'
    root = _write_tmp({"jarvis/vcs/git_channel.py": content})
    v = cwp.scan_paths(os.path.join(root, "jarvis"))
    assert not v, f"git_channel.py 应被豁免: {v}"


def test_main_returns_1_on_violation():
    root = _write_tmp({"jarvis/evil.py": 'git push origin main\n'})
    code = cwp.main(["--root", root])
    assert code == 1


def test_main_returns_0_when_clean():
    root = _write_tmp({"jarvis/ok.py": 'x = 1  # 无害\n'})
    code = cwp.main(["--root", root])
    assert code == 0
