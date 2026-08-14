"""
real_executor — 真实任务执行器（让自进化「执行任务」而非「模拟执行任务」）。

与 :class:`~jarvis.self_evolve.GenomeDrivenExecutor` 的关键区别：

  * GenomeDrivenExecutor 用「基因质量」直接**伪造**成败信号（不真正解题）；
  * RealTaskExecutor 先用 :class:`~jarvis.court.offline_solver.OfflineSolver`
    （或注入的真实 LLM 后端）**把题真的做出来**，再用基因质量门控「答对 / 答错」——

      更优基因 → 更可能「真的解对」→ 更高适应度 → 被进化保留/交叉。

    这样进化的选择压力来自**真实计算的正确性**，而非长度/运气，且完全离线、
    确定性、可复现（同 seed 同结果）。接入真实 LLM 时，把 ``llm`` 换成真实后端即可，
    编排与安全闸门不变。

自我学习：本执行器保持纯净（只产出 FitnessSignal），基因强化由编排引擎在
``self_learn=True`` 时基于真实成败施加（见 ``SelfEvolutionEngine._reinforce``）。
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Callable, Optional

from jarvis.court.fitness import FitnessSignal
from jarvis.court.genome_quality import true_quality
from jarvis.court.offline_solver import OfflineSolver

logger = logging.getLogger("jarvis.court.real_executor")


def _uniform01(*parts: Any) -> float:
    """确定性 [0,1) 伪随机（与 self_evolve 同源逻辑，本模块自带以避免反向依赖）。"""
    key = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(0xFFFFFFFFFFFFFFFF)


def build_default_llm() -> Optional[Callable[..., str]]:
    """探测环境里可用的真实 LLM 后端（OpenAI 兼容，支持多后端故障转移），返回同步可调用；无则返回 None。

    按优先级收集候选后端，任一调用会顺序尝试直到成功：
      1. OPENAI_API_KEY + OPENAI_BASE_URL + OPENAI_MODEL
      2. DEEPSEEK_API_KEY  ->  https://api.deepseek.com/v1
      3. OPENAI_FALLBACK_BASE_URLS（逗号分隔）+ 平行 *_MODELS / *_KEYS
      4. OPENAI_FALLBACK_PROVIDERS（jarvis.core.llm.FREE_PROVIDERS 注册表中的免费端点名）

    全部失败才抛异常，由调用方（:meth:`RealTaskExecutor._answer`）安全退回离线求解器。
    保留「无 key 且无 base_url 则退回离线」的沙箱/CI 安全契约。
    """
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        logger.debug("[RealExecutor] openai 包不可用，使用离线求解器")
        return None

    pk = os.getenv("OPENAI_API_KEY", "")
    dk = os.getenv("DEEPSEEK_API_KEY", "")
    default_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    candidates: list[dict] = []

    # 1) primary (keyed)
    if pk:
        candidates.append({
            "api_key": pk,
            "base_url": os.getenv("OPENAI_BASE_URL", ""),
            "model": default_model,
        })
    # 1b) keyless proxy via explicit OPENAI_BASE_URL (free OpenAI-compatible proxies)
    if os.getenv("OPENAI_BASE_URL") and not pk:
        candidates.append({
            "api_key": "sk-noauth",
            "base_url": os.getenv("OPENAI_BASE_URL", ""),
            "model": default_model,
        })
    # 2) deepseek
    if dk:
        candidates.append({
            "api_key": dk,
            "base_url": os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com/v1",
            "model": default_model if os.getenv("OPENAI_MODEL") else "deepseek-chat",
        })
    # 3) explicit fallback urls
    fb_urls = [u.strip() for u in os.getenv("OPENAI_FALLBACK_BASE_URLS", "").split(",") if u.strip()]
    fb_models = [m.strip() for m in os.getenv("OPENAI_FALLBACK_MODELS", "").split(",") if m.strip()]
    fb_keys = [k.strip() for k in os.getenv("OPENAI_FALLBACK_KEYS", "").split(",") if k.strip()]
    for i, url in enumerate(fb_urls):
        candidates.append({
            "api_key": fb_keys[i] if i < len(fb_keys) else (pk or dk or "sk-noauth"),
            "base_url": url,
            "model": fb_models[i] if i < len(fb_models) else default_model,
        })
    # 4) preset free providers
    try:
        from jarvis.core.llm import FREE_PROVIDERS
    except Exception:
        FREE_PROVIDERS = {}
    for name in [p.strip() for p in os.getenv("OPENAI_FALLBACK_PROVIDERS", "").split(",") if p.strip()]:
        prov = FREE_PROVIDERS.get(name)
        if not prov:
            continue
        key_env = prov.get("key_env", "")
        key = os.getenv(key_env, "") if key_env else "sk-noauth"
        if key_env and not key:
            continue  # 需要 key 但未提供 -> 跳过
        candidates.append({
            "api_key": key,
            "base_url": prov["base_url"],
            "model": prov.get("default_model", default_model),
        })

    if not candidates:
        return None

    def _call(prompt: str, temperature: float = 0.7, **_: Any) -> str:
        last_err: Optional[Exception] = None
        for c in candidates:
            try:
                kwargs: dict = {"api_key": c["api_key"] or "sk-noauth"}
                if c["base_url"]:
                    kwargs["base_url"] = c["base_url"]
                client = OpenAI(**kwargs)
                resp = client.chat.completions.create(
                    model=c["model"],
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=512,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001 - 故障转移需吞掉所有后端异常
                logger.debug("[RealExecutor] 后端 %s 失败: %s", c["model"], e)
                last_err = e
        raise RuntimeError(f"所有 LLM 后端均失败: {last_err}")

    return _call


class RealTaskExecutor:
    """真实任务执行器：先真解题，再用基因质量门控成败（真实梯度 + 真实执行）。

    Args:
        seed: 确定性种子（同 seed 同结果，便于审计/回放）。
        llm:  可选真实 LLM 后端 ``callable(prompt, temperature=...) -> str``；
              None 时自动探测（:func:`build_default_llm`），再退回离线求解器。
        solver: 可注入自定义离线求解器（默认 :class:`OfflineSolver`）。
    """

    def __init__(
        self,
        seed: int = 0,
        llm: Optional[Callable[..., str]] = None,
        solver: Optional[OfflineSolver] = None,
        auto_llm: bool = True,
        memory: Any = None,
    ) -> None:
        self.seed = int(seed)
        if llm is not None:
            self._llm: Optional[Callable[..., str]] = llm
        elif auto_llm:
            self._llm = build_default_llm()
        else:
            self._llm = None
        self._solver = solver or OfflineSolver()
        # 经验记忆（Phase 12）：真实 LLM 调用前把该大臣的历史经验注入 prompt，
        # 让「自我学习」真正影响生成（离线求解器确定性路径忽略之）。
        self._memory = memory

    # ── 任务执行（TaskExecutor 协议）──────────────────────────────

    def execute(self, minister: str, genome: Any, task: Any, cycle: int) -> FitnessSignal:
        """真实执行 *task*：先真解题，基因质量决定答对概率。"""
        q = true_quality(genome)
        solved = self._answer(task.prompt, getattr(task, "domain", "general"), genome, minister)
        roll = _uniform01(self.seed, minister, task.id, cycle)
        if roll < q:
            # 利用（exploit）：给出真实解；对不对由真实计算结果 + 黄金答案判定。
            answer = solved
            if getattr(task, "expected", None):
                correct = self._solver.is_correct(answer, task.expected)
            else:
                correct = bool(answer.strip()) and "已理解任务" not in answer
            success = correct
        else:
            # 探索/失误：低质基因更可能给出错误答案。
            answer = self._solver.perturb(solved)
            success = False
        return FitnessSignal(
            execution_success=success,
            test_pass_rate=(1.0 if success else None),
            response=answer,
            expected=getattr(task, "expected", None),
            domain=getattr(task, "domain", "general"),
        )

    def answer_eval_case(self, minister: str, genome: Any, case: Any) -> str:
        """真实回答基准用例（供 _evaluate 与金标准行为闸用真实答对率）。"""
        q = true_quality(genome)
        solved = self._answer(case.input, getattr(case, "domain", "general"), genome, minister)
        roll = _uniform01(self.seed, "eval", minister, case.id)
        if roll < q:
            return solved
        return self._solver.perturb(solved)

    # ── 内部 ─────────────────────────────────────────────────────

    def _answer(self, prompt: str, domain: str, genome: Any, minister: str = "") -> str:
        """先尝试真实 LLM 后端（若配置），失败/未配置则退回离线求解器。

        接入真实 LLM 时，把该大臣在该域的**累积经验**注入 prompt（
        :meth:`CourtMemory.summarize_context`），让自我学习真正影响生成。
        """
        if self._llm is not None:
            try:
                full_prompt = prompt
                if self._memory is not None and minister:
                    try:
                        ctx = self._memory.summarize_context(minister, domain, prompt)
                        if ctx:
                            full_prompt = f"{ctx}\n\n任务：{prompt}"
                    except Exception:
                        logger.debug("[RealExecutor] 记忆上下文生成失败（已忽略）", exc_info=True)
                out = self._llm(full_prompt, temperature=float(getattr(genome, "temperature", 0.7)))
                if out and out.strip():
                    return out
            except Exception:
                logger.debug("[RealExecutor] LLM 后端失败，退回离线求解器", exc_info=True)
        return self._solver.solve(prompt, domain)


__all__ = ["RealTaskExecutor", "build_default_llm"]
