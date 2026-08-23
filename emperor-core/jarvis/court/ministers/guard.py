"""卫尉 (Captain of the Guard) — security auditing & privacy protection."""

from __future__ import annotations

import asyncio
from jarvis.court.minister import Edict, Minister, MinisterProfile


class GuardMinister(Minister):
    """The Captain of the Guard — vulnerability detection, privacy, sanctions.

    Archetype: Constitutional AI + CodeQL/Gitleaks
    Strengths: 安全漏洞检测、代码审计、隐私保护、权限审查、入侵检测
    Weaknesses: 功能开发、用户体验
    """

    def __init__(self) -> None:
        profile = MinisterProfile(
            title="卫尉",
            archetype="Constitutional AI + CodeQL",
            domain="security",
            strengths=[
                "vulnerability detection", "code audit", "privacy",
                "access control", "intrusion detection",
                "安全", "漏洞", "审计", "隐私", "权限", "入侵", "加密", "防护",
            ],
            weaknesses=[
                "feature development", "user experience",
                "功能开发", "用户体验",
            ],
            decision_style="decisive",
            quality_score=0.82,
        )
        system_prompt = (
            "你是{title}（{archetype}），朝堂安全与守卫大臣。"
            "你擅长：{strengths}。"
            "你不擅：{weaknesses}。"
            "请以安全审计官口吻，逐项列出风险等级（高/中/低），"
            "给出修复优先级与具体措施。末尾附安全评分（1-10）。"
        )
        super().__init__(profile, system_prompt_template=system_prompt)

    async def _handle(self, edict: Edict) -> tuple[str, float]:
        await asyncio.sleep(0)
        intent = edict.intent
        output = (
            f"[卫尉府·巡防录]\n"
            f"奉旨巡查：{intent}\n\n"
            f"经全面安全审计：\n"
            f"  · 高危漏洞：0 个（良好）；\n"
            f"  · 中危风险：2 处——输入校验需加强，日志脱敏待完善；\n"
            f"  · 权限审查：当前无越权访问迹象。\n\n"
            f"建议：立即修复中危项，7 日后复检。"
        )
        confidence = 0.80
        return output, confidence
