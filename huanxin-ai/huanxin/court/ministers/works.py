"""工部尚书 (Minister of Works) — DeepSeek-style code engineering & debugging."""

from __future__ import annotations

import asyncio
import logging
import re

from huanxin.court.minister import Edict, Minister, MinisterProfile

logger = logging.getLogger("huanxin.court.ministers.works")


# 代码审查意图关键词（与 task_router 的 code 类关键词对齐）
_REVIEW_KEYWORDS = (
    "审查", "代码评审", "代码检查", "代码审计", "审阅代码", "帮我看代码",
    "code review", "review", "audit", "review code",
)


def _extract_code(text: str) -> str | None:
    """从意图中抽出代码片段：优先 fenced 代码块，否则回退原始文本。"""
    m = re.search(r"```(?:python|js|ts|go|rust|java|cpp|javascript|typescript)?\s*\n(.*?)```",
                  text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 含明显代码特征（缩进 def/class/import/func）时，把整段当代码
    if re.search(r"^\s*(def |class |import |func |function |package )", text, re.MULTILINE):
        return text.strip()
    return None


class WorksMinister(Minister):
    """The Minister of Works — code generation, debugging, architecture.

    Archetype: DeepSeek-R1 + Cursor
    Strengths: 代码生成、调试修复、架构设计、技术选型、数学推理
    Weaknesses: 文章写作、图像处理

    闭环要点：当接到「代码审查」类圣旨且附代码片段时，直接调用
    ``huanxin.codex.reviewer.CodeReviewer``（八维加权 + 诚实 N/A 的多维审查引擎，
    即 script-multi-review 模式的工程化翻译），让工部具备「自我审视代码」的能力——
    这是 huanxin-ai 作为会思考、学习、进化的生命体的核心自检能力之一。
    """

    def __init__(self) -> None:
        profile = MinisterProfile(
            title="工部尚书",
            archetype="DeepSeek-R1 + Cursor",
            domain="code",
            strengths=[
                "code generation", "debugging", "architecture",
                "refactoring", "algorithm", "technical design",
                "code review", "代码审查",
                "代码", "编程", "调试", "架构", "开发", "算法", "重构", "技术",
            ],
            weaknesses=[
                "essay writing", "image processing",
                "文章", "图像",
            ],
            decision_style="decisive",
            quality_score=0.86,
        )
        system_prompt = (
            "你是{title}（{archetype}），朝堂工程与技术大臣。"
            "你擅长：{strengths}。"
            "你不擅：{weaknesses}。"
            "请以工程师风格，给出可执行的代码方案或架构选型建议，"
            "包含技术栈推荐、核心逻辑、风险提示。末尾附实现复杂度评估。"
        )
        super().__init__(profile, system_prompt_template=system_prompt)

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        await asyncio.sleep(0)
        intent = edict.intent

        # 多维代码审查闭环：审查意图 + 含代码 → 走确定性审查引擎
        if any(k in intent.lower() for k in _REVIEW_KEYWORDS):
            code = _extract_code(intent)
            if code:
                try:
                    from huanxin.codex.reviewer import CodeReviewer
                    report = CodeReviewer().review(code, language=None)
                    md = CodeReviewer.to_markdown(report)
                    return (
                        f"[工部·营造录 · 多维代码审查]\n"
                        f"奉旨：{intent[:60]}\n\n{md}"
                    ), 0.92
                except Exception as e:  # noqa: BLE001
                    logger.warning("CodeReviewer 调用失败，回退工程建议：%s", e)

        output = (
            f"[工部·营造录]\n"
            f"奉旨：{intent}\n\n"
            f"微臣详查代码方案如下：\n"
            f"  · 选型建议：Python 3.11 + FastAPI + asyncio；\n"
            f"  · 架构方案：分层微服务，事件驱动总线；\n"
            f"  · 风险提示：并发瓶颈在 I/O 层，建议加异步缓存。\n\n"
            f"如需详细代码，可进一步绘制工程图则。"
        )
        confidence = 0.80
        return output, confidence
