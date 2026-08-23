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
import time
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

    后端解析统一交给 :func:`jarvis.core.llm._resolve_backends_from_env`，与
    emperor 主路径（:func:`jarvis.core.llm.build_manager_from_env`）共用同一份
    逻辑，因此 ``NVIDIA_MODEL`` / ``ARK_MODEL`` 等 ``model_env`` 覆盖在自进化闭环
    里也会生效（修复此前两条链路不一致的问题）。

    调用顺序：主端点 → OPENAI_FALLBACK_BASE_URLS → OPENAI_FALLBACK_PROVIDERS 注册表。
    全部失败才抛异常，由调用方（:meth:`RealTaskExecutor._answer`）安全退回离线求解器。
    保留「无 key 且无 base_url 则退回离线」的沙箱/CI 安全契约。

    韧性增强（完整优化）：
      * 每后端最多重试 ``LLM_MAX_RETRIES`` 次（指数退避）。
      * 按 ``requests_per_minute``（免费端点注册表里已配置，如 NVIDIA=40）做最小间隔限流。
    """
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        logger.debug("[RealExecutor] openai 包不可用，使用离线求解器")
        return None

    try:
        from jarvis.core.llm import _resolve_backends_from_env, _safe_int, _safe_float
    except Exception:
        logger.debug("[RealExecutor] 无法导入核心 LLM 解析器，退回离线求解器")
        return None

    resolved = _resolve_backends_from_env()
    if not resolved:
        return None

    max_retries = _safe_int(os.getenv("LLM_MAX_RETRIES", "0"), 0)
    backoff = _safe_float(os.getenv("LLM_RETRY_BACKOFF", "0.3"), 0.3)

    candidates: list[dict] = []
    for cfg in resolved:
        candidates.append({
            "api_key": cfg.api_key or "sk-noauth",
            "base_url": cfg.base_url or "",
            "model": cfg.model,
            "rpm": int(getattr(cfg, "requests_per_minute", 0) or 0),
        })
    # Keep only *reachable* backends: a candidate must have a real base_url or a
    # real api_key. A pure-mock primary (no base, dummy key) is dropped so we don't
    # waste a call on api.openai.com and so "no key -> offline" stays intact.
    def _is_live(c: dict) -> bool:
        return bool(c["base_url"]) or c["api_key"] not in ("", "sk-noauth")
    candidates = [c for c in candidates if _is_live(c)]
    if not candidates:
        return None

    _last_ts: dict[int, float] = {}

    def _call(prompt: str, temperature: float = 0.7, **_: Any) -> str:
        last_err: Optional[Exception] = None
        for c in candidates:
            # ── 限流：按 rpm 保证最小调用间隔（免费端点防 429）──
            rpm = c["rpm"]
            if rpm > 0:
                wait = (60.0 / rpm) - (time.time() - _last_ts.get(id(c), 0.0))
                if wait > 0:
                    time.sleep(wait)
            _last_ts[id(c)] = time.time()
            # ── 重试：指数退避（默认 0 次，不破坏既有行为）──
            attempts = max(1, max_retries + 1)
            for attempt in range(attempts):
                try:
                    kwargs: dict = {"api_key": c["api_key"]}
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
                    last_err = e
                    if attempt < attempts - 1:
                        time.sleep(backoff * (2 ** attempt))
                        logger.debug("[RealExecutor] 后端 %s 第 %d 次重试", c["model"], attempt + 1)
                        continue
                    logger.debug("[RealExecutor] 后端 %s 失败: %s", c["model"], e)
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
