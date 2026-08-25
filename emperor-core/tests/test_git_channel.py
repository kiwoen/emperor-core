"""P2.1 GitWriteChannel 单元测试。

核心断言：
- propose_change 产出分支名以 ``absorb-`` 开头；
- 命令序列中**绝不**出现 ``git push origin master/main``（受保护分支）；
- gh api 开 PR 时 head=新分支、base=受保护分支；
- 任何试图把受保护分支当作写回目标（branch=master）都立即抛 ValueError；
- _assert_no_protected_push 能拦住非法命令序列。
"""

from __future__ import annotations

import pytest

from huanxin.vcs.git_channel import GitWriteChannel, PROTECTED_BRANCHES


class _FakeRunner:
    """记录所有 git 子命令，返回成功结果（不真正执行）。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        # 模拟 git push 成功
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


class _FakeGh:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        return type("R", (), {"returncode": 0, "stdout": '{"url":"https://github.com/x/y/pull/1"}', "stderr": ""})()


def _make_channel():
    runner = _FakeRunner()
    gh = _FakeGh()
    ch = GitWriteChannel(runner=runner, gh_runner=gh, keep_workdir=True)
    return ch, runner, gh


def test_propose_creates_absorb_branch_and_opens_pr():
    ch, runner, gh = _make_channel()
    result = ch.propose_change(
        repo="kiwoen/huanxin-ai",
        patch_text="diff --git a/x b/x\n",
        title="auto-absorb: test",
        base="master",
        date_tag="2026-08-12",
    )
    assert result.branch.startswith("absorb-")
    assert result.base == "master"
    # gh api 被调用，且 head/base 正确
    gh_args = " ".join(gh.calls[0])
    assert "head=" + result.branch in gh_args
    assert "base=master" in gh_args


def test_no_push_to_protected_branch():
    ch, runner, gh = _make_channel()
    ch.propose_change(
        repo="kiwoen/huanxin-ai",
        patch_text="diff --git a/x b/x\n",
        title="t",
        base="master",
        date_tag="2026-08-12",
    )
    # 关键不变量：推送命令的目标绝不可能是 master/main
    for cmd in runner.calls:
        if cmd[0] == "git" and cmd[1] == "push":
            assert cmd[-1] not in PROTECTED_BRANCHES
    # 显式反向校验
    GitWriteChannel._assert_no_protected_push(runner.calls)


def test_open_pr_targets_protected_base():
    ch, runner, gh = _make_channel()
    result = ch.propose_change(
        repo="o/n", patch_text="x", title="t", base="main", date_tag="2026-08-12",
    )
    assert result.base == "main"
    assert "base=main" in " ".join(gh.calls[0])


def test_refuses_branch_equal_to_protected():
    ch, runner, gh = _make_channel()
    with pytest.raises(ValueError):
        ch.propose_change(
            repo="o/n", patch_text="x", title="t",
            base="master", branch="master",
        )


def test_assert_no_protected_push_detects_violation():
    with pytest.raises(RuntimeError):
        GitWriteChannel._assert_no_protected_push([
            ["git", "push", "origin", "master"],
        ])
    # 合法：push 到普通吸收分支
    GitWriteChannel._assert_no_protected_push([
        ["git", "push", "origin", "absorb-2026-08-12-draft"],
    ])
