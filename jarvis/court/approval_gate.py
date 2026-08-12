"""
approval_gate — 把「自进化写回」接入既有的人工审批门（HITL）。

DGM 论文（arXiv:2505.22954）的安全模型是三段式：**沙箱 + 基准评测 + 人类审批门**。
前面的工作已经落实了沙箱（GitWriteChannel 只开 PR、绝不直推 master）和基准评测
（WritebackGate），但**写回前还缺「人类审批」这一环的显式落地**——尽管项目里
早就有一个完整的 :class:`~jarvis.approval.ApprovalEngine` 审批引擎，自进化循环
却从未使用它。

本模块把二者接通：
    - 每次写回前，向 ApprovalEngine 创建一条 ``risk_level=critical`` 的审批请求
      （自我改写代码 = 最高风险操作）；
    - ``auto_approve=False``（默认/生产）：仅**记录请求**、返回 ``pending``，
      真正的 PR 必须等人类对这条请求 approve 后才由另一条流程发起——绝不自动合；
    - ``auto_approve=True``（离线演示 / CI）：自动 approve，便于跑通闭环；
    - 没有注入 ApprovalEngine 时退化为「直接放行」（向后兼容，等同原行为）。

这样，自进化系统的「人类审批门」就不再是纸面约束，而是真实落到了已有的审批
数据库（``approval.db``）里——谁、何时、为何批准了一次自我改写，全部可查、可审计。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("jarvis.court.approval_gate")


@dataclass
class ApprovalOutcome:
    """一次写回审批裁决的结果。"""

    status: str          # "approved" | "pending" | "skipped"
    request_id: Optional[str] = None
    note: str = ""

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return f"Approval[{self.status}]" + (f":{self.request_id}" if self.request_id else "")


class WritebackApprovalGate:
    """把自进化写回接入 ApprovalEngine 的审批门。

    Args:
        approval_engine: 既有的 :class:`~jarvis.approval.ApprovalEngine` 实例；
            ``None`` 表示不启用审批（退化为直接放行）。
        auto_approve: 是否自动批准（仅用于离线演示 / CI，绝不用于生产默认）。
    """

    def __init__(self, approval_engine: Any = None, auto_approve: bool = False) -> None:
        self._engine = approval_engine
        self._auto = bool(auto_approve)

    def decide(
        self, repo: str, branch: str, diff: str, cycle: int, title: str = "",
    ) -> ApprovalOutcome:
        """对一次拟写回的进化产物做审批裁决。

        Args:
            repo: 目标仓库 ``owner/name``。
            branch: 拟开 PR 的分支。
            diff: 进化产物的真实 diff（用于审计留痕，最长截取 4000 字符）。
            cycle: 进化轮序号。
            title: 写回标题。

        Returns:
            :class:`ApprovalOutcome`。``status=="approved"`` 时调用方才可继续写回。
        """
        # 无引擎：退化为直接放行（向后兼容）
        if self._engine is None:
            return ApprovalOutcome(status="approved", request_id=None,
                                  note="no approval engine configured")

        # 自我改写代码 = 最高风险操作
        req = self._engine.create_request(
            task_id=f"self-evolve-c{cycle}",
            prompt=f"自进化写回：向 {repo} 的 {branch} 提案改动",
            domain="system",
            capability="code.write",
            extra={
                "repo": repo,
                "branch": branch,
                "title": title,
                "diff_preview": (diff or "")[:4000],
                "risk": "自进化系统对自身基因的改写，需人工复核",
            },
        )

        if self._auto:
            self._engine.approve(req.id, note="auto-approve (offline/CI demo)")
            logger.info("[ApprovalGate] 自动批准写回请求 %s（离线/CI）", req.id)
            return ApprovalOutcome(status="approved", request_id=req.id,
                                  note="auto-approved")

        logger.warning(
            "[ApprovalGate] 写回请求 %s 已创建，待人工 approve 后才可发起 PR",
            req.id,
        )
        return ApprovalOutcome(status="pending", request_id=req.id,
                               note="awaiting human approval")


__all__ = ["WritebackApprovalGate", "ApprovalOutcome"]
