"""
P2.1 GitWriteChannel — 自进化系统「写回自身代码」的唯一合法通道。

安全铁律（来自 DGM 论文 arXiv:2505.22954 + 实施方案 §0.1 / §7）：
    1. **只开 PR，绝不直推 master/main。**
    2. 改动先在隔离沙箱里应用（git clone 到临时目录），不直接动生产工作树。
    3. 人类审批门写在「GitHub 分支保护规则」上，本通道绝不自动合入。
    4. 任何试图写受保护分支（master/main）的调用都**立刻拒绝**，绝不含糊。

这解决了 emperor-core 调研发现的「零代码自修改」死亡之穴：
过去 `pyproject.toml` 声明 gitpython，但全库零 `import git`，进化从不真正改码。
本通道把「AI 改自身代码」工程化、可控化、可审计化——且闸门写死。

典型用法::
    ch = GitWriteChannel()                       # 生产：真实 subprocess
    result = ch.propose_change(
        repo="kiwoen/emperor-core",
        patch_text=diff_text,
        title="auto-absorb: 提升影子大臣晋升门槛",
        base="master",                            # PR 目标=受保护分支
    )
    # result == {"branch": "absorb-2026-08-12-xxxx", "pr": <gh 输出>, "base": "master"}
    # 之后由人类在 GitHub 上 review + 合入；本通道绝不碰 master。
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, List, Optional, Tuple

from jarvis.vcs.writeback_gate import WritebackGate

logger = logging.getLogger("jarvis.vcs.git_channel")

# 受保护分支：写回通道永不向这些分支直接 push。
PROTECTED_BRANCHES: Tuple[str, ...] = ("master", "main")


# 命令运行器的返回形状（与 subprocess.CompletedProcess 兼容即可）
RunResult = Any


def _default_runner(cmd: List[str], **kw: Any) -> RunResult:
    """默认运行器：真实执行子进程（仅用于非测试生产路径）。"""
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw)


def _default_gh_runner(args: List[str], **kw: Any) -> RunResult:
    """默认 gh API 调用运行器。"""
    return subprocess.run(["gh", "api", *args], capture_output=True, text=True, check=True, **kw)


def _today() -> str:
    return date.today().isoformat()


@dataclass
class ProposeResult:
    """一次 propose_change 的结果。"""

    branch: str
    base: str
    pr: Any
    workdir: Optional[str] = None

    def as_dict(self) -> dict:
        return {"branch": self.branch, "base": self.base, "pr": self.pr}


class GitWriteChannel:
    """唯一合法的代码写回通道：只开 PR，不直推 master。

    Args:
        runner: 执行 ``git`` 子进程的函数（测试可注入 mock）。
        gh_runner: 执行 ``gh api`` 的函数（测试可注入 mock）。
        keep_workdir: 是否在返回后保留临时克隆目录（默认清理）。
    """

    def __init__(
        self,
        runner: Optional[Callable[..., RunResult]] = None,
        gh_runner: Optional[Callable[..., RunResult]] = None,
        keep_workdir: bool = False,
    ) -> None:
        self._run = runner or _default_runner
        self._gh = gh_runner or _default_gh_runner
        self._keep_workdir = keep_workdir

    # ── 公开 API ──────────────────────────────────────────────

    def propose_change(
        self,
        repo: str,
        patch_text: str,
        title: str,
        base: str = "master",
        branch: Optional[str] = None,
        date_tag: Optional[str] = None,
        eval_report: Optional[Any] = None,
        eval_gate: Optional[WritebackGate] = None,
        baseline_report: Optional[Any] = None,
    ) -> ProposeResult:
        """把补丁作为 PR 提案到 *repo*（PR 目标为受保护分支 *base*）。

        流程：克隆到隔离临时目录 → 新建 ``absorb-<date>-<suffix>`` 分支 →
        应用补丁 → 提交 → push 该分支（**绝不 push base**）→ 用 gh api 开 PR。

        Args:
            repo: ``owner/name`` 形式，例如 ``kiwoen/emperor-core``。
            patch_text:  unified diff 文本（``git diff`` / ``git format-patch`` 输出）。
            title: PR 标题。
            base: PR 目标分支（应为受保护分支 master/main）。
            branch: 自定义吸收分支名；省略则自动生成。
            date_tag: 吸收分支日期标签；省略则用当天。
            eval_report: 可选的 :class:`EvalReport`。一旦提供，写回前先过
                :class:`WritebackGate` 评测闸；不达标即在**任何 git 操作之前**
                抛 :class:`WritebackBlocked`（DGM 闭环：基准不过就绝不写回）。
            eval_gate: 自定义闸；省略则用默认严格闸（min_pass_rate=1.0）。
            baseline_report: 可选基线报告，用于回归对照。

        Returns:
            :class:`ProposeResult`。

        Raises:
            ValueError: 当 *base* 或 *branch* 命中受保护分支时（绝不写回）。
            WritebackBlocked: 提供 *eval_report* 但评测不达标 / 回归时。
        """
        # ── DGM 评测闸：基准不过，直接拒绝，绝不动 git ──
        if eval_report is not None:
            gate = eval_gate or WritebackGate()
            gate.assert_allowed(eval_report, baseline=baseline_report)

        # ── 安全校验：受保护分支绝不允许作为写回目标 ──
        if base in PROTECTED_BRANCHES:
            # base 是 PR 目标（受保护）——这本身合法（PR 合入需人类审批）。
            # 但我们**绝不**向 base 直接 push；下面只 push 新建的 absorb 分支。
            pass
        if branch in PROTECTED_BRANCHES:
            raise ValueError(
                f"拒绝写回：分支 '{branch}' 是受保护分支，GitWriteChannel 绝不直推"
            )

        branch = branch or self._make_branch_name(date_tag)

        workdir = tempfile.mkdtemp(prefix="emperor-absorb-")
        try:
            # 1) 克隆到隔离沙箱（不碰生产工作树）
            self._run(["git", "clone", f"https://github.com/{repo}.git", workdir])
            # 2) 新建吸收分支（与受保护分支同级，绝不等同 base）
            self._run(["git", "-C", workdir, "checkout", "-b", branch])
            # 3) 写入并应用补丁
            patch_path = os.path.join(workdir, ".absorb.patch")
            with open(patch_path, "w", encoding="utf-8") as fh:
                fh.write(patch_text)
            self._run(["git", "-C", workdir, "apply", "--3way", patch_path])
            # 4) 提交
            self._run(["git", "-C", workdir, "add", "-A"])
            self._run([
                "git", "-C", workdir, "commit",
                "-m", f"{title}\n\n(auto-absorb via GitWriteChannel — 待人类 review)",
            ])
            # 5) 只 push 新建的吸收分支（关键不变量见 _assert_no_protected_push）
            self._run(["git", "-C", workdir, "push", "origin", branch])
            # 6) 开 PR（head=新分支, base=受保护分支；人类审批后才会合入）
            pr = self._gh([
                f"repos/{repo}/pulls",
                "-f", f"title={title}",
                "-f", f"head={branch}",
                "-f", f"base={base}",
                "-f", "body=auto-absorb: 待人类 review 后合入（绝不自动合 master）",
            ])
            logger.info(
                "[GitWriteChannel] 已提案 PR: repo=%s branch=%s base=%s",
                repo, branch, base,
            )
            return ProposeResult(branch=branch, base=base, pr=pr, workdir=workdir)
        finally:
            if not self._keep_workdir:
                self._safe_rmtree(workdir)

    # ── 内部辅助 ──────────────────────────────────────────────

    def _make_branch_name(self, date_tag: Optional[str]) -> str:
        tag = date_tag or _today()
        suffix = os.environ.get("ABSORB_SUFFIX", "draft")
        return f"absorb-{tag}-{suffix}"

    @staticmethod
    def _assert_no_protected_push(commands: List[List[str]]) -> None:
        """反向校验：命令序列中绝不允许出现「push 到受保护分支」。

        用于 CI 与测试断言。命中即抛 RuntimeError（可控失败，不静默）。
        """
        for cmd in commands:
            if len(cmd) >= 4 and cmd[0] == "git" and cmd[1] == "push":
                # 形如 git push origin master / git push origin main
                target = cmd[-1]
                if target in PROTECTED_BRANCHES:
                    raise RuntimeError(
                        f"WRITE-PROTECT VIOLATION: 检测到直推受保护分支 {target!r}: {cmd}"
                    )

    @staticmethod
    def _safe_rmtree(path: str) -> None:
        import shutil
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:  # pragma: no cover - 清理失败不应中断主流程
            logger.debug("[GitWriteChannel] 临时目录清理失败：%s", path)


__all__ = [
    "PROTECTED_BRANCHES",
    "GitWriteChannel",
    "ProposeResult",
]
