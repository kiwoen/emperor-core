"""
服务流水线引擎 — 将能力串联成端到端的自动化服务链
实现"服务规模化"：专家级、个性化、持续、普惠

P0.3: 集成逐步故障恢复引擎 (Step-Level Failure Recovery Engine)
  设置 use_recovery_engine=True 即可为每个 Stage 自动包裹
  RecoveryEngine（重试 + 熔断 + 降级）。
"""
from __future__ import annotations

import datetime
import json as _json
import sched
import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any, Optional
from enum import Enum


class StageStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass
class StageResult:
    """流水线阶段结果"""
    stage_name: str
    status: StageStatus
    started_at: float = 0
    finished_at: float = 0
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class PipelineResult:
    """流水线执行结果"""
    pipeline_name: str
    pipeline_id: str
    status: PipelineStatus = PipelineStatus.IDLE
    stages: List[StageResult] = field(default_factory=list)
    started_at: float = 0
    finished_at: float = 0
    final_output: Dict[str, Any] = field(default_factory=dict)


class ServicePipeline:
    """
    服务流水线：将多个能力阶段串联执行

    Stage handler 约定：
      handler(ctx: dict) -> dict
      其中 ctx 是当前流水线上下文（包含所有前置阶段的输出），
      handler 内部负责调用实际的 capability handler（签名 prompt + **kwargs）。

    使用方式：
        pipeline = ServicePipeline("每日简报", [
            Stage("gather", lambda ctx: _news_handler("科技新闻")),
            Stage("analyze", lambda ctx: _handle_text(ctx["gather"]["result"])),
        ])
        result = pipeline.execute()

    P0.3 故障恢复 (use_recovery_engine=True)：
        每个 Stage 自动获得：重试(指数退避+抖动) + 熔断器 + 降级链。
        与 auto_retry 兼容：auto_retry 是旧版简单重试，
        use_recovery_engine 提供完整的生产级恢复能力。
    """

    def __init__(self, name: str, stages: List['Stage'] = None,
                 auto_retry: bool = True, max_retries: int = 1,
                 use_recovery_engine: bool = False,
                 recovery_engine_config: Dict[str, Any] = None,
                 on_stage_complete: Callable = None,
                 on_pipeline_complete: Callable = None):
        self.name = name
        self.stages = stages or []
        self.auto_retry = auto_retry
        self.max_retries = max_retries
        self.use_recovery_engine = use_recovery_engine
        self.recovery_engine_config = recovery_engine_config or {}
        self.on_stage_complete = on_stage_complete
        self.on_pipeline_complete = on_pipeline_complete
        self._status = PipelineStatus.IDLE
        self._current_stage_index = 0
        self._lock = threading.Lock()
        self._context: Dict[str, Any] = {}  # 阶段间上下文传递

        # Lazy-init recovery engine instance
        self._recovery_engine = None
        self._recovery_results: List = []  # 记录每阶段的恢复结果

    @property
    def status(self) -> PipelineStatus:
        return self._status

    @property
    def progress(self) -> Dict[str, Any]:
        """流水线进度"""
        total = len(self.stages)
        completed = sum(
            1 for s in self.stages
            if s._result and s._result.status in (StageStatus.SUCCESS, StageStatus.SKIPPED)
        )
        return {
            "pipeline": self.name,
            "status": self._status.value,
            "total_stages": total,
            "completed_stages": completed,
            "current_stage": (
                self.stages[self._current_stage_index].name
                if self._current_stage_index < total else None
            ),
            "percentage": (completed / total * 100) if total > 0 else 0,
        }

    def add_stage(self, stage: 'Stage') -> 'ServicePipeline':
        self.stages.append(stage)
        return self

    def execute(self, initial_context: Dict[str, Any] = None) -> PipelineResult:
        """执行流水线"""
        import uuid

        pipeline_id = str(uuid.uuid4())[:8]
        result = PipelineResult(
            pipeline_name=self.name,
            pipeline_id=pipeline_id,
            status=PipelineStatus.RUNNING,
            started_at=time.time(),
        )

        self._status = PipelineStatus.RUNNING
        self._context = initial_context or {}

        for i, stage in enumerate(self.stages):
            self._current_stage_index = i

            # 检查条件
            if stage.condition and not stage.condition(self._context):
                stage_result = StageResult(
                    stage_name=stage.name,
                    status=StageStatus.SKIPPED,
                )
                result.stages.append(stage_result)
                continue

            # 执行阶段（含重试）
            stage_result = self._execute_stage(stage)
            result.stages.append(stage_result)

            # 更新上下文
            if stage.output_key and stage_result.status == StageStatus.SUCCESS:
                self._context[stage.output_key] = stage_result.result

            # 回调
            if self.on_stage_complete:
                self.on_stage_complete(stage.name, stage_result)

            # 失败处理
            if stage_result.status == StageStatus.FAILED:
                if stage.fail_strategy == "stop":
                    result.status = PipelineStatus.FAILED
                    break
                elif stage.fail_strategy == "skip":
                    continue

        else:
            result.status = PipelineStatus.COMPLETED

        result.finished_at = time.time()
        result.final_output = self._context
        self._status = result.status

        if self.on_pipeline_complete:
            self.on_pipeline_complete(result)

        return result

    def _execute_stage(self, stage: 'Stage') -> StageResult:
        """执行单个阶段（含重试 / 故障恢复）"""
        if self.use_recovery_engine:
            return self._execute_stage_with_recovery(stage)
        else:
            return self._execute_stage_legacy(stage)

    def _execute_stage_legacy(self, stage: 'Stage') -> StageResult:
        """旧版执行逻辑：简单重试（保持向后兼容）"""
        max_attempts = self.max_retries + 1 if self.auto_retry else 1

        for attempt in range(max_attempts):
            stage_result = StageResult(
                stage_name=stage.name,
                status=StageStatus.RUNNING,
                started_at=time.time(),
            )

            try:
                stage_input = dict(self._context)
                if stage.input_mapping:
                    stage_input.update(stage.input_mapping(self._context))

                output = stage.handler(stage_input)

                stage_result.status = StageStatus.SUCCESS
                stage_result.result = output if isinstance(output, dict) else {"raw": str(output)}
                stage_result.finished_at = time.time()
                stage._result = stage_result
                return stage_result

            except Exception as e:
                if attempt < max_attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue

                stage_result.status = StageStatus.FAILED
                stage_result.error = str(e)
                stage_result.finished_at = time.time()
                stage._result = stage_result
                return stage_result

        return stage_result  # unreachable

    def _execute_stage_with_recovery(self, stage: 'Stage') -> StageResult:
        """使用 RecoveryEngine 执行阶段：重试 + 熔断 + 降级"""
        from jarvis.failure_recovery import (
            RecoveryEngine, RecoveryContext, RecoveryResultStatus,
            ErrorClassifier, RetryPolicy, CircuitBreaker, FallbackChain,
            FallbackAction, FallbackType, create_default_engine,
        )

        # 获取或创建 RecoveryEngine 实例
        engine = self._get_recovery_engine()

        stage_result = StageResult(
            stage_name=stage.name,
            status=StageStatus.RUNNING,
            started_at=time.time(),
        )

        # 构建 handler 包装：确保输入来自当前上下文
        def wrapped_handler():
            stage_input = dict(self._context)
            if stage.input_mapping:
                stage_input.update(stage.input_mapping(self._context))
            return stage.handler(stage_input)

        try:
            recovery_result = engine.execute_with_recovery(
                wrapped_handler,
                context=RecoveryContext(
                    stage_name=stage.name,
                    pipeline_name=self.name,
                ),
            )
            self._recovery_results.append(recovery_result)

            if recovery_result.status in (RecoveryResultStatus.SUCCESS,
                                          RecoveryResultStatus.RETRY_SUCCESS,
                                          RecoveryResultStatus.DEGRADED):
                stage_result.status = StageStatus.SUCCESS
                output = recovery_result.output
                stage_result.result = output if isinstance(output, dict) else {"raw": str(output)}
                stage_result.finished_at = time.time()
                stage._result = stage_result
                return stage_result
            else:
                # FAILED or CIRCUIT_OPEN
                stage_result.status = StageStatus.FAILED
                stage_result.error = recovery_result.error or "Recovery exhausted"
                stage_result.finished_at = time.time()
                stage._result = stage_result
                return stage_result

        except Exception as e:
            # Recovery engine itself threw — wrap as failure
            stage_result.status = StageStatus.FAILED
            stage_result.error = str(e)
            stage_result.finished_at = time.time()
            stage._result = stage_result
            return stage_result

    def _get_recovery_engine(self):
        """Lazy-init the recovery engine instance."""
        if self._recovery_engine is None:
            from jarvis.failure_recovery import (
                RecoveryEngine, ErrorClassifier, RetryPolicy,
                CircuitBreaker, FallbackChain, FallbackAction, FallbackType,
            )

            config = self.recovery_engine_config

            classifier = ErrorClassifier()
            retry = RetryPolicy(
                max_retries=config.get("max_retries", 3),
                base_delay=config.get("base_delay", 0.5),
                max_delay=config.get("max_delay", 30.0),
                jitter=config.get("jitter", True),
                timeout=config.get("timeout"),
            )
            breaker = CircuitBreaker(
                name=f"pipeline:{self.name}",
                failure_threshold=config.get("failure_threshold", 5),
                recovery_timeout=config.get("recovery_timeout", 30.0),
            )

            fallback = FallbackChain()
            if config.get("fallback_default") is not None:
                fallback.add(FallbackAction(
                    FallbackType.DEFAULT,
                    default_value=config["fallback_default"],
                    priority=0,
                ))
            fallback.add(FallbackAction(FallbackType.SKIP, priority=100))

            self._recovery_engine = RecoveryEngine(
                classifier=classifier,
                retry_policy=retry,
                circuit_breaker=breaker,
                fallback_chain=fallback,
                name=f"pipeline:{self.name}",
            )
        return self._recovery_engine

    def get_recovery_stats(self) -> Optional[Dict[str, Any]]:
        """获取故障恢复统计（仅 use_recovery_engine=True 时有值）"""
        if self._recovery_engine:
            return self._recovery_engine.get_stats()
        return None

    def get_recovery_results(self) -> List:
        """获取每阶段的恢复详细结果"""
        return list(self._recovery_results)


class Stage:
    """流水线阶段

    handler 约定：`handler(ctx: dict) -> dict`
    其中 ctx 是合并后的上下文字典，handler 内部负责调用实际的能力函数。
    """

    def __init__(self, name: str, handler: Callable[[Dict], Any],
                 input_mapping: Callable[[Dict], Dict] = None,
                 output_key: str = None,
                 condition: Callable[[Dict], bool] = None,
                 fail_strategy: str = "stop"):  # stop | skip | continue
        self.name = name
        self.handler = handler
        self.input_mapping = input_mapping  # 从上下文提取输入
        self.output_key = output_key  # 输出写入上下文的 key
        self.condition = condition  # 前置条件
        self.fail_strategy = fail_strategy
        self._result: Optional[StageResult] = None


class PipelineRegistry:
    """流水线注册中心 — 管理和调度所有服务流水线"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._pipelines: Dict[str, ServicePipeline] = {}
            cls._instance._templates: Dict[str, callable] = {}
            cls._instance._history: List[PipelineResult] = []
            cls._instance._lock = threading.Lock()
        return cls._instance

    def register_template(self, name: str, factory: callable):
        """注册流水线模板（工厂函数）"""
        self._templates[name] = factory

    def create_pipeline(self, template_name: str, **kwargs) -> ServicePipeline:
        """从模板创建流水线实例"""
        if template_name not in self._templates:
            raise ValueError(f"Unknown pipeline template: {template_name}")

        pipeline = self._templates[template_name](**kwargs)
        pipeline_id = f"{template_name}_{len(self._history)}"
        self._pipelines[pipeline_id] = pipeline
        return pipeline

    def execute_template(self, template_name: str, context: Dict = None, **kwargs) -> PipelineResult:
        """创建并执行流水线"""
        pipeline = self.create_pipeline(template_name, **kwargs)
        result = pipeline.execute(context)

        with self._lock:
            self._history.append(result)
            # 只保留最近 100 条
            if len(self._history) > 100:
                self._history = self._history[-100:]

        return result

    def get_history(self, limit: int = 20) -> List[Dict]:
        """获取执行历史"""
        return [
            {
                "pipeline_name": r.pipeline_name,
                "pipeline_id": r.pipeline_id,
                "status": r.status.value,
                "stages": [{"name": s.stage_name, "status": s.status.value} for s in r.stages],
                "duration": round(r.finished_at - r.started_at, 2) if r.finished_at else 0,
            }
            for r in self._history[-limit:]
        ]

    def get_active_pipelines(self) -> List[Dict]:
        """获取正在运行的流水线"""
        return [
            p.progress for p in self._pipelines.values()
            if p.status == PipelineStatus.RUNNING
        ]


# 全局单例
pipeline_registry = PipelineRegistry()


# === 预定义流水线模板 ===
# 注意：各 Stage handler 以 ctx dict 为入参，内部自行将 ctx["key"] 中的
# 数据转换为实际 handler 的 (prompt, **kwargs) 调用格式。


def _create_daily_brief_pipeline():
    """每日简报流水线：新闻采集 → 文本统计 → 摘要格式化"""
    from jarvis.capability import _news_handler, _handle_text

    return (ServicePipeline("每日简报", auto_retry=True, max_retries=1)
        .add_stage(Stage(
            name="采集新闻",
            handler=lambda ctx: _news_handler("科技新闻"),
            output_key="news_raw",
        ))
        .add_stage(Stage(
            name="文本统计",
            handler=lambda ctx: {
                "summary": _handle_text(
                    ctx.get("news_raw", {}).get("result", "(无内容)")
                )["result"]
            },
            input_mapping=lambda ctx: {
                "news_text": ctx.get("news_raw", {}).get("result", ""),
            },
            output_key="analysis",
            fail_strategy="skip",
        ))
        .add_stage(Stage(
            name="格式化输出",
            handler=lambda ctx: {
                "report": (
                    "=== 每日简报 ===\n\n"
                    + str(ctx.get("news_raw", {}).get("result", "(无新闻数据)"))
                    + "\n\n--- 文本分析 ---\n"
                    + str(ctx.get("analysis", {}).get("summary", ""))
                )
            },
            output_key="final_report",
            fail_strategy="skip",
        ))
    )


def _create_health_check_pipeline():
    """健康检查流水线：系统健康 → 天气 → 综合摘要"""
    from jarvis.capability import _weather_handler

    return (ServicePipeline("健康检查", auto_retry=False)
        .add_stage(Stage(
            name="系统健康",
            handler=lambda ctx: (
                __import__('jarvis.health', fromlist=['get_system_health'])
                .get_system_health()
            ),
            output_key="system_health",
        ))
        .add_stage(Stage(
            name="天气查询",
            handler=lambda ctx: _weather_handler("北京天气"),
            output_key="weather",
            fail_strategy="skip",
        ))
    )


def _create_search_analyze_pipeline(query: str = ""):
    """搜索分析流水线：搜索 → 抓取 → 文本分析"""
    from jarvis.capability import (
        _web_search_handler, _web_fetch_handler, _handle_text,
    )

    q = query or ""

    return (ServicePipeline(f"搜索分析: {q[:20]}" if q else "搜索分析", auto_retry=True)
        .add_stage(Stage(
            name="搜索",
            handler=lambda ctx: _web_search_handler(
                ctx.get("query", q)
            ),
            input_mapping=lambda ctx: {"query": q},
            output_key="search_results",
        ))
        .add_stage(Stage(
            name="抓取详情",
            handler=lambda ctx: _web_fetch_handler(
                ctx.get("search_results", {}).get("result", "")
            ),
            output_key="fetched_content",
            fail_strategy="skip",
        ))
        .add_stage(Stage(
            name="分析总结",
            handler=lambda ctx: {
                "analysis": _handle_text(
                    ctx.get("fetched_content", {}).get("result", "(无抓取内容)")
                )["result"]
            },
            output_key="analysis",
            fail_strategy="skip",
        ))
    )


# 注册默认模板
pipeline_registry.register_template("daily_brief", _create_daily_brief_pipeline)
pipeline_registry.register_template("health_check", _create_health_check_pipeline)
pipeline_registry.register_template("search_analyze", _create_search_analyze_pipeline)


# ═══════════════════════════════════════════════════════════════════
# Pipeline Scheduler — automated continuous service execution
# ═══════════════════════════════════════════════════════════════════


class PipelineScheduler:
    """流水线定时调度器 — 让服务流水线持续自动运行"""

    def __init__(self, registry: PipelineRegistry = None):
        self.registry = registry or pipeline_registry
        self._scheduler = sched.scheduler(time.time, time.sleep)
        self._jobs: Dict[str, Dict] = {}  # job_id -> {template, interval, next_run, ...}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def add_schedule(self, job_id: str, template_name: str,
                     interval_minutes: int = 60,
                     context: Dict = None,
                     cron_expr: str = None) -> str:
        """添加定时流水线

        Args:
            job_id: 唯一标识
            template_name: 流水线模板名
            interval_minutes: 间隔分钟数（cron_expr 为 None 时使用）
            context: 执行上下文
            cron_expr: cron 表达式（"分 时 日 月 周"），优先于 interval_minutes
        """
        if template_name not in self.registry._templates:
            raise ValueError(f"Unknown template: {template_name}")

        with self._lock:
            self._jobs[job_id] = {
                "template": template_name,
                "interval_minutes": interval_minutes,
                "cron_expr": cron_expr,
                "context": context or {},
                "enabled": True,
                "run_count": 0,
                "last_run": None,
                "last_result": None,
                "next_run": self._calc_next_run(interval_minutes, cron_expr),
            }
        return job_id

    def remove_schedule(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                return True
            return False

    def enable_job(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["enabled"] = True
                self._jobs[job_id]["next_run"] = self._calc_next_run(
                    self._jobs[job_id]["interval_minutes"],
                    self._jobs[job_id]["cron_expr"]
                )
                return True
            return False

    def disable_job(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["enabled"] = False
                self._jobs[job_id]["next_run"] = None
                return True
            return False

    def get_jobs(self) -> List[Dict]:
        """获取所有调度任务"""
        with self._lock:
            return [
                {
                    "job_id": jid,
                    "template": job["template"],
                    "interval_minutes": job["interval_minutes"],
                    "cron_expr": job["cron_expr"],
                    "enabled": job["enabled"],
                    "run_count": job["run_count"],
                    "last_run": job["last_run"],
                    "next_run": job["next_run"],
                    "last_result": job["last_result"]["status"] if job["last_result"] else None,
                }
                for jid, job in self._jobs.items()
            ]

    def start(self):
        """启动调度器（后台线程）"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="pipeline-scheduler")
        self._thread.start()

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _run_loop(self):
        """调度循环"""
        while self._running:
            now = time.time()
            next_job_time = None

            with self._lock:
                for job_id, job in self._jobs.items():
                    if not job["enabled"]:
                        continue

                    next_run = job.get("next_run")
                    if next_run and now >= next_run:
                        # 执行流水线
                        try:
                            result = self.registry.execute_template(
                                job["template"],
                                context=job["context"]
                            )
                            job["last_result"] = {
                                "status": result.status.value,
                                "pipeline_id": result.pipeline_id,
                            }
                        except Exception as e:
                            job["last_result"] = {"status": "error", "error": str(e)}

                        job["run_count"] += 1
                        job["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        job["next_run"] = self._calc_next_run(
                            job["interval_minutes"],
                            job["cron_expr"]
                        )

                    if job["enabled"] and job.get("next_run"):
                        if next_job_time is None or job["next_run"] < next_job_time:
                            next_job_time = job["next_run"]

            # 睡眠到下一个任务时间
            if next_job_time:
                sleep_seconds = max(1, next_job_time - time.time())
                time.sleep(min(sleep_seconds, 30))  # 最多睡 30 秒，避免错过
            else:
                time.sleep(30)

    def _calc_next_run(self, interval_minutes: int, cron_expr: str = None) -> float:
        """计算下次执行时间（Unix timestamp）"""
        now = datetime.datetime.now()

        if cron_expr:
            # 简单 cron 解析：分 时 日 月 周
            return self._next_cron_time(now, cron_expr)

        # 固定间隔
        return time.time() + interval_minutes * 60

    def _next_cron_time(self, now: datetime.datetime, cron_expr: str) -> float:
        """计算下一个 cron 匹配时间"""
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return time.time() + 3600  # fallback: 1 小时

        minute_part, hour_part, day_part, month_part, weekday_part = parts

        # 简单实现：匹配每小时/每天的情况
        # 例如 "0 8 * * *" = 每天 8:00
        try:
            target_minute = int(minute_part) if minute_part != "*" else 0
            target_hour = int(hour_part) if hour_part != "*" else now.hour
        except ValueError:
            return time.time() + 3600

        next_run = now.replace(minute=target_minute, second=0, microsecond=0)

        if day_part == "*":
            next_run = next_run.replace(hour=target_hour)
            if next_run <= now:
                next_run += datetime.timedelta(days=1)

        return next_run.timestamp()


# 全局调度器单例
pipeline_scheduler = PipelineScheduler()
