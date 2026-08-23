"""测试服务流水线系统"""
import pytest
from jarvis.pipeline import (
    ServicePipeline, Stage, StageStatus, PipelineStatus,
    PipelineRegistry, pipeline_registry,
    PipelineScheduler,
)


class TestStage:
    def test_stage_creation(self):
        stage = Stage("test", lambda ctx: {"result": "ok"})
        assert stage.name == "test"
        assert stage.fail_strategy == "stop"

    def test_stage_with_output_key(self):
        stage = Stage("test", lambda ctx: {"data": 42}, output_key="test_output")
        assert stage.output_key == "test_output"

    def test_stage_with_condition(self):
        stage = Stage("cond", lambda ctx: {"ran": True}, condition=lambda ctx: False)
        assert stage.condition is not None

    def test_stage_with_fail_skip(self):
        stage = Stage("skip_fail", lambda ctx: {}, fail_strategy="skip")
        assert stage.fail_strategy == "skip"


class TestServicePipeline:
    def test_simple_pipeline(self):
        p = ServicePipeline("test")
        p.add_stage(Stage("step1", lambda ctx: {"a": 1}, output_key="step1"))
        p.add_stage(Stage(
            "step2",
            lambda ctx: {"b": ctx.get("step1", {}).get("a", 0) + 1},
            output_key="step2",
        ))

        result = p.execute()
        assert result.status == PipelineStatus.COMPLETED
        assert len(result.stages) == 2
        assert all(s.status == StageStatus.SUCCESS for s in result.stages)
        assert result.final_output["step2"]["b"] == 2

    def test_pipeline_stop_on_failure(self):
        calls = []
        p = ServicePipeline("test")
        p.add_stage(Stage("step1", lambda ctx: (_ for _ in ()).throw(Exception("fail"))))
        p.add_stage(Stage("step2", lambda ctx: calls.append("executed") or {"ok": True}))

        result = p.execute()
        assert result.status == PipelineStatus.FAILED
        assert result.stages[0].status == StageStatus.FAILED
        assert len(calls) == 0  # step2 should not execute

    def test_pipeline_skip_on_failure(self):
        p = ServicePipeline("test")
        p.add_stage(Stage(
            "step1",
            lambda ctx: (_ for _ in ()).throw(Exception("fail")),
            fail_strategy="skip",
        ))
        p.add_stage(Stage("step2", lambda ctx: {"ok": True}, output_key="step2"))

        result = p.execute()
        assert result.status == PipelineStatus.COMPLETED
        assert result.stages[0].status == StageStatus.FAILED
        assert result.stages[1].status == StageStatus.SUCCESS

    def test_pipeline_context_passing(self):
        p = ServicePipeline("test")
        p.add_stage(Stage("gather", lambda ctx: {"value": 100}, output_key="gather"))
        p.add_stage(Stage(
            "process",
            lambda ctx: {"doubled": ctx.get("gather", {}).get("value", 0) * 2},
            output_key="process",
        ))

        result = p.execute()
        assert result.final_output["process"]["doubled"] == 200

    def test_pipeline_condition_skip(self):
        p = ServicePipeline("test")
        p.add_stage(Stage("always_run", lambda ctx: {"ran": True}, output_key="step1"))
        p.add_stage(Stage(
            "conditional",
            lambda ctx: {"should_not_run": True},
            condition=lambda ctx: False,  # always skip
        ))

        result = p.execute()
        assert result.stages[0].status == StageStatus.SUCCESS
        assert result.stages[1].status == StageStatus.SKIPPED

    def test_pipeline_progress(self):
        p = ServicePipeline("test")
        p.add_stage(Stage("s1", lambda ctx: {"a": 1}))
        p.add_stage(Stage("s2", lambda ctx: {"b": 2}))

        progress = p.progress
        assert progress["total_stages"] == 2
        assert progress["completed_stages"] == 0

    def test_pipeline_with_initial_context(self):
        p = ServicePipeline("test")
        p.add_stage(Stage("use_init", lambda ctx: {"val": ctx.get("init_val", 0)}, output_key="used"))

        result = p.execute(initial_context={"init_val": 99})
        assert result.final_output["used"]["val"] == 99

    def test_pipeline_retry_on_failure(self):
        attempts = []

        def flaky_handler(ctx):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("transient error")
            return {"ok": True}

        p = ServicePipeline("test", auto_retry=True, max_retries=3)
        p.add_stage(Stage("flaky", flaky_handler, output_key="flaky"))

        result = p.execute()
        assert result.stages[0].status == StageStatus.SUCCESS
        assert len(attempts) == 3

    def test_pipeline_retry_exhausted(self):
        attempts = []

        def always_fail(ctx):
            attempts.append(1)
            raise RuntimeError("permanent error")

        p = ServicePipeline("test", auto_retry=True, max_retries=1)
        p.add_stage(Stage("fail", always_fail))

        result = p.execute()
        assert result.stages[0].status == StageStatus.FAILED
        assert len(attempts) == 2  # 1 + 1 retry

    def test_input_mapping(self):
        p = ServicePipeline("test")
        p.add_stage(Stage(
            "double",
            lambda ctx: {"result": ctx.get("value", 0) * 2},
            input_mapping=lambda ctx: {"value": 5},
            output_key="doubled",
        ))

        result = p.execute()
        assert result.final_output["doubled"]["result"] == 10

    def test_empty_pipeline(self):
        p = ServicePipeline("empty")
        result = p.execute()
        assert result.status == PipelineStatus.COMPLETED
        assert len(result.stages) == 0

    def test_non_dict_handler_output(self):
        p = ServicePipeline("test")
        p.add_stage(Stage("string_out", lambda ctx: "plain text", output_key="text"))

        result = p.execute()
        assert result.stages[0].status == StageStatus.SUCCESS
        assert result.final_output["text"]["raw"] == "plain text"

    def test_stage_result_has_timestamps(self):
        p = ServicePipeline("test")
        p.add_stage(Stage("timed", lambda ctx: {"ok": True}))

        result = p.execute()
        stage = result.stages[0]
        assert stage.started_at > 0
        assert stage.finished_at > 0
        assert stage.finished_at >= stage.started_at


class TestPipelineRegistry:
    def test_singleton(self):
        r1 = PipelineRegistry()
        r2 = PipelineRegistry()
        assert r1 is r2

    def test_register_and_list_templates(self):
        reg = PipelineRegistry()
        reg.register_template("test_template", lambda: ServicePipeline("test"))
        assert "test_template" in reg._templates

    def test_execute_template(self):
        reg = PipelineRegistry()
        reg.register_template(
            "echo",
            lambda **kw: ServicePipeline("echo").add_stage(
                Stage(
                    "echo",
                    lambda ctx: {"msg": kw.get("msg", "hello")},
                    output_key="echo",
                )
            ),
        )

        result = reg.execute_template("echo", msg="world")
        assert result.status == PipelineStatus.COMPLETED
        assert result.final_output["echo"]["msg"] == "world"

    def test_history_tracking(self):
        reg = PipelineRegistry()
        reg.register_template(
            "quick",
            lambda: ServicePipeline("quick").add_stage(
                Stage("ping", lambda ctx: {"pong": True}, output_key="ping")
            ),
        )

        reg.execute_template("quick")
        history = reg.get_history()
        assert len(history) >= 1
        assert history[-1]["pipeline_name"] == "quick"
        assert history[-1]["status"] == "completed"

    def test_get_active_pipelines(self):
        reg = PipelineRegistry()
        active = reg.get_active_pipelines()
        assert isinstance(active, list)

    def test_unknown_template_raises(self):
        reg = PipelineRegistry()
        with pytest.raises(ValueError, match="Unknown pipeline template"):
            reg.create_pipeline("nonexistent")

    def test_create_pipeline_from_template(self):
        reg = PipelineRegistry()
        reg.register_template("simple", lambda: ServicePipeline("simple"))
        p = reg.create_pipeline("simple")
        assert isinstance(p, ServicePipeline)
        assert p.name == "simple"


class TestPipelineTemplates:
    """验证预定义模板可正常创建并执行"""

    def test_daily_brief_template_creation(self):
        from jarvis.pipeline import pipeline_registry as pr

        p = pr.create_pipeline("daily_brief")
        assert p.name == "每日简报"
        assert len(p.stages) == 3
        assert p.stages[0].name == "采集新闻"
        assert p.stages[1].name == "文本统计"
        assert p.stages[2].name == "格式化输出"

    def test_health_check_template_creation(self):
        from jarvis.pipeline import pipeline_registry as pr

        p = pr.create_pipeline("health_check")
        assert p.name == "健康检查"
        assert len(p.stages) == 2
        assert p.stages[0].name == "系统健康"
        assert p.stages[1].name == "天气查询"

    def test_search_analyze_template_creation(self):
        from jarvis.pipeline import pipeline_registry as pr

        p = pr.create_pipeline("search_analyze", query="test query")
        assert p.name.startswith("搜索分析")
        assert len(p.stages) == 3
        assert p.stages[0].name == "搜索"
        assert p.stages[1].name == "抓取详情"
        assert p.stages[2].name == "分析总结"


class TestPipelineScheduler:
    def test_add_schedule(self):
        scheduler = PipelineScheduler()
        scheduler.registry.register_template(
            "test_sched",
            lambda: ServicePipeline("test").add_stage(
                Stage("s1", lambda ctx: {"ok": True}, output_key="s1")
            ),
        )

        job_id = scheduler.add_schedule("test_job", "test_sched", interval_minutes=60)
        assert job_id == "test_job"
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0]["template"] == "test_sched"

    def test_add_schedule_unknown_template(self):
        scheduler = PipelineScheduler()
        with pytest.raises(ValueError):
            scheduler.add_schedule("bad", "nonexistent", 60)

    def test_remove_schedule(self):
        scheduler = PipelineScheduler()
        scheduler.registry.register_template(
            "test_sched",
            lambda: ServicePipeline("test").add_stage(
                Stage("s1", lambda ctx: {"ok": True})
            ),
        )

        scheduler.add_schedule("j1", "test_sched", 60)
        assert scheduler.remove_schedule("j1")
        assert not scheduler.remove_schedule("j1")
        assert len(scheduler.get_jobs()) == 0

    def test_enable_disable(self):
        scheduler = PipelineScheduler()
        scheduler.registry.register_template(
            "test_sched",
            lambda: ServicePipeline("test").add_stage(
                Stage("s1", lambda ctx: {"a": 1})
            ),
        )

        scheduler.add_schedule("j1", "test_sched", 60)
        assert scheduler.get_jobs()[0]["enabled"]

        scheduler.disable_job("j1")
        assert not scheduler.get_jobs()[0]["enabled"]

        scheduler.enable_job("j1")
        assert scheduler.get_jobs()[0]["enabled"]

    def test_start_stop(self):
        scheduler = PipelineScheduler()
        scheduler.start()
        assert scheduler._running
        scheduler.stop()
        assert not scheduler._running
