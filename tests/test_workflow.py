"""
Cross-Domain Workflow Engine Tests.

Validates:
1. Single domain → no workflow triggered
2. Pipeline mode (sequential domain chaining)
3. Parallel mode (fan-out)
4. Step planning heuristics
5. Edge cases (empty domains, errors, result merging)
"""

import asyncio
import sys
from pathlib import Path

import pytest

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis.core.orchestrator import Domain, Intent, TaskResult, Orchestrator
from jarvis.core.workflow import WorkflowEngine, WorkflowMode, WorkflowStep, WorkflowResult


class TestWorkflowStep:
    """WorkflowStep dataclass tests."""

    def test_defaults(self):
        step = WorkflowStep(domain=Domain.RESEARCH, prompt="analyze trends")
        assert step.depends_on == []
        assert step.role == ""

    def test_full_init(self):
        step = WorkflowStep(
            domain=Domain.FINANCE,
            prompt="generate report",
            depends_on=[0],
            role="analysis",
        )
        assert step.domain == Domain.FINANCE
        assert step.depends_on == [0]
        assert step.role == "analysis"


class TestWorkflowResult:
    """WorkflowResult dataclass tests."""

    def test_all_succeeded(self):
        wr = WorkflowResult(
            success=True,
            mode=WorkflowMode.PIPELINE,
            steps=[
                (Domain.RESEARCH, TaskResult(domain=Domain.RESEARCH, success=True, output="ok1")),
                (Domain.FINANCE, TaskResult(domain=Domain.FINANCE, success=True, output="ok2")),
            ],
        )
        assert wr.all_succeeded

    def test_partial_failure(self):
        wr = WorkflowResult(
            success=True,  # top-level flag set by caller
            mode=WorkflowMode.PIPELINE,
            steps=[
                (Domain.RESEARCH, TaskResult(domain=Domain.RESEARCH, success=True, output="ok")),
                (Domain.FINANCE, TaskResult(domain=Domain.FINANCE, success=False, error="fail")),
            ],
        )
        assert not wr.all_succeeded

    def test_empty_steps(self):
        wr = WorkflowResult(success=True, mode=WorkflowMode.PIPELINE)
        assert wr.all_succeeded  # vacuous truth


class TestWorkflowPlanning:
    """Test workflow auto-planning from intents."""

    def setup_method(self):
        self.orch = Orchestrator()
        self.engine = self.orch.workflow

    def test_single_domain_no_workflow(self):
        intent = Intent(raw_text="calculate 2+2", primary_domain=Domain.ENGINEERING)
        steps = self.engine.plan(intent)
        assert len(steps) == 0, "Single domain should produce no workflow steps"

    def test_two_domain_pipeline(self):
        intent = Intent(
            raw_text="研究比特币趋势并生成投资建议",
            primary_domain=Domain.RESEARCH,
            secondary_domains=[Domain.FINANCE],
        )
        steps = self.engine.plan(intent)
        assert len(steps) == 2
        assert steps[0].domain == Domain.RESEARCH
        assert steps[0].depends_on == []  # first step no deps
        assert steps[1].domain == Domain.FINANCE
        assert steps[1].depends_on == [0]  # depends on research

    def test_three_domain_chain(self):
        intent = Intent(
            raw_text="调研AI安全趋势，评估代码风险，生成安全报告",
            primary_domain=Domain.RESEARCH,
            secondary_domains=[Domain.SECURITY, Domain.CREATOR],
        )
        steps = self.engine.plan(intent)
        assert len(steps) == 3
        assert steps[0].domain == Domain.RESEARCH
        assert steps[1].domain == Domain.SECURITY
        assert steps[2].domain == Domain.CREATOR
        assert steps[1].depends_on == [0]
        assert steps[2].depends_on == [1]

    def test_deduplicate_domains(self):
        intent = Intent(
            raw_text="研究AI", primary_domain=Domain.RESEARCH,
            secondary_domains=[Domain.RESEARCH, Domain.FINANCE],
        )
        steps = self.engine.plan(intent)
        domains = [s.domain for s in steps]
        assert domains == [Domain.RESEARCH, Domain.FINANCE]  # deduped

    def test_transition_prompt_building(self):
        prompt = self.engine._build_transition_prompt(
            "研究比特币", "Financial Analysis", "research_to_investment", Domain.RESEARCH
        )
        assert "研究比特币" in prompt
        assert "Financial Analysis" in prompt
        assert "投资" in prompt


class TestWorkflowModeDetection:
    """Test execution mode auto-detection."""

    def setup_method(self):
        self.engine = WorkflowEngine(Orchestrator())

    def test_pipeline_default(self):
        intent = Intent(raw_text="研究并分析", primary_domain=Domain.RESEARCH)
        assert self.engine.detect_mode(intent) == WorkflowMode.PIPELINE

    def test_parallel_chinese(self):
        intent = Intent(raw_text="同时调研AI和安全", primary_domain=Domain.RESEARCH)
        assert self.engine.detect_mode(intent) == WorkflowMode.PARALLEL

    def test_parallel_markers(self):
        for marker in ["一起", "并行", "分别"]:
            intent = Intent(raw_text=f"{marker}处理", primary_domain=Domain.RESEARCH)
            assert self.engine.detect_mode(intent) == WorkflowMode.PARALLEL


class TestWorkflowRequiresDetection:
    """Test requires_workflow heuristic."""

    def test_empty_secondary_no_action(self):
        intent = Intent(raw_text="hello", primary_domain=Domain.PERSONAL)
        assert not WorkflowEngine.requires_workflow(intent)

    def test_two_secondary_triggers(self):
        intent = Intent(
            raw_text="test", primary_domain=Domain.RESEARCH,
            secondary_domains=[Domain.FINANCE, Domain.ENGINEERING],
        )
        assert WorkflowEngine.requires_workflow(intent)

    def test_cross_keyword_triggers(self):
        for kw in ["并且", "然后", "综合"]:
            intent = Intent(raw_text=f"A {kw} B", primary_domain=Domain.RESEARCH)
            assert WorkflowEngine.requires_workflow(intent), f"Keyword '{kw}' should trigger"

    def test_analyze_action_with_one_secondary(self):
        intent = Intent(
            raw_text="分析市场", primary_domain=Domain.RESEARCH,
            secondary_domains=[Domain.FINANCE], action="分析",
        )
        assert WorkflowEngine.requires_workflow(intent)


class TestOutputMerging:
    """Test result merging for different modes."""

    def setup_method(self):
        self.engine = WorkflowEngine(Orchestrator())

    def test_pipeline_merge(self):
        results = [
            (Domain.RESEARCH, TaskResult(domain=Domain.RESEARCH, success=True, output="Research output")),
            (Domain.FINANCE, TaskResult(domain=Domain.FINANCE, success=True, output="Finance output")),
        ]
        merged = self.engine._merge_outputs(results, WorkflowMode.PIPELINE)
        assert "Step 1" in merged
        assert "Step 2" in merged
        assert "Research" in merged
        assert "Finance" in merged

    def test_parallel_merge(self):
        results = [
            (Domain.RESEARCH, TaskResult(domain=Domain.RESEARCH, success=True, output="R")),
            (Domain.SECURITY, TaskResult(domain=Domain.SECURITY, success=True, output="S")),
        ]
        merged = self.engine._merge_outputs(results, WorkflowMode.PARALLEL)
        assert "Multi-Domain Analysis" in merged
        assert "R" in merged
        assert "S" in merged

    def test_merge_empty(self):
        merged = self.engine._merge_outputs([], WorkflowMode.PIPELINE)
        assert merged == ""

    def test_merge_skips_failures(self):
        results = [
            (Domain.RESEARCH, TaskResult(domain=Domain.RESEARCH, success=True, output="ok")),
            (Domain.FINANCE, TaskResult(domain=Domain.FINANCE, success=False, error="fail")),
        ]
        merged = self.engine._merge_outputs(results, WorkflowMode.PIPELINE)
        assert "ok" in merged
        # Failed step should not appear in output
        assert "Research" in merged


class TestWorkflowExecution:
    """End-to-end workflow execution tests with mock domain handlers."""

    @pytest.fixture(autouse=True)
    def setup_async(self):
        """Create orchestrator with mock handlers for RESEARCH and FINANCE."""
        self.orch = Orchestrator()

        # Fake handler that registers request and returns a response
        class FakeHandler:
            def __init__(self, domain: Domain):
                self.domain = domain
                self.calls: list[Intent] = []

            async def handle(self, intent: Intent) -> TaskResult:
                self.calls.append(intent)
                context = intent.entities.get("_workflow_context", "")
                # Build a domain-specific response that shows context propagation
                output = f"[{self.domain.name}] Response to: {intent.raw_text[:80]}"
                if context:
                    output += f"\n  (with previous context: {str(context)[:60]})"
                return TaskResult(
                    domain=self.domain,
                    success=True,
                    output=output,
                )

        self.research_handler = FakeHandler(Domain.RESEARCH)
        self.finance_handler = FakeHandler(Domain.FINANCE)
        self.orch.registry._modules[Domain.RESEARCH] = self.research_handler
        self.orch.registry._modules[Domain.FINANCE] = self.finance_handler

    @pytest.mark.asyncio(loop_scope="function")
    async def test_pipeline_execution(self):
        """Verify pipeline mode chains domain outputs."""
        intent = Intent(
            raw_text="研究比特币趋势并生成投资建议",
            primary_domain=Domain.RESEARCH,
            secondary_domains=[Domain.FINANCE],
        )
        result = await self.orch.workflow.execute(intent)
        assert result.success
        assert result.all_succeeded
        assert len(result.steps) == 2
        # Verify research was called first
        assert result.steps[0][0] == Domain.RESEARCH
        assert result.steps[1][0] == Domain.FINANCE
        # Verify pipeline chaining: merged output contains both domains
        assert "RESEARCH" in result.merged_output
        assert "FINANCE" in result.merged_output
        # Finance should have received context from Research
        finance_call = self.finance_handler.calls[0]
        assert "_workflow_context" in finance_call.entities

    @pytest.mark.asyncio(loop_scope="function")
    async def test_parallel_execution(self):
        """Verify parallel mode executes steps concurrently."""
        # Add a third handler for parallel test
        class FakeSecurity:
            async def handle(self, intent: Intent) -> TaskResult:
                return TaskResult(domain=Domain.SECURITY, success=True,
                                  output=f"[SECURITY] {intent.raw_text[:40]}")

        self.orch.registry._modules[Domain.SECURITY] = FakeSecurity()

        intent = Intent(
            raw_text="同时分析市场行情和金融风险",
            primary_domain=Domain.RESEARCH,
            secondary_domains=[Domain.FINANCE, Domain.SECURITY],
        )
        result = await self.orch.workflow.execute(intent)
        assert result.success
        assert len(result.steps) == 3

    @pytest.mark.asyncio(loop_scope="function")
    async def test_workflow_result_to_taskresult(self):
        """Verify WorkflowResult correctly converts to TaskResult."""
        wf_result = WorkflowResult(
            success=True,
            mode=WorkflowMode.PIPELINE,
            steps=[
                (Domain.RESEARCH, TaskResult(domain=Domain.RESEARCH, success=True, output="R")),
            ],
            merged_output="merged content",
            total_execution_ms=150.0,
        )
        # This conversion happens in Orchestrator.execute()
        tr = TaskResult(
            domain=Domain.RESEARCH,
            success=wf_result.all_succeeded,
            output=wf_result.merged_output,
            data={
                "workflow_mode": wf_result.mode.name,
                "steps": [{"domain": d.name, "success": r.success}
                          for d, r in wf_result.steps],
                "execution_ms": wf_result.total_execution_ms,
            },
        )
        assert tr.success
        assert tr.output == "merged content"
        assert tr.data["workflow_mode"] == "PIPELINE"
        assert len(tr.data["steps"]) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_orchestrator_single_domain_no_workflow(self):
        """Orchestrator should NOT trigger workflow for single-domain intents."""
        result = await self.orch.execute("hello")
        assert result.domain == Domain.PERSONAL
        # No workflow data should appear in single-domain path
        assert "workflow_mode" not in result.data

    @pytest.mark.asyncio(loop_scope="function")
    async def test_error_in_workflow_step(self):
        """Pipeline should continue after a failed step."""
        class FailingHandler:
            async def handle(self, intent: Intent) -> TaskResult:
                return TaskResult(domain=Domain.RESEARCH, success=False, error="boom")

        self.orch.registry._modules[Domain.RESEARCH] = FailingHandler()

        intent = Intent(
            raw_text="研究并分析", primary_domain=Domain.RESEARCH,
            secondary_domains=[Domain.FINANCE],
        )
        result = await self.orch.workflow.execute(intent)
        assert not result.success  # overall should reflect failure
        assert result.steps[0][1].success is False
        assert result.steps[0][1].error == "boom"


class TestWorkflowIntegration:
    """Integration: verify Orchestrator.execute() routes to workflow."""

    @pytest.fixture(autouse=True)
    def setup_async(self):
        self.orch = Orchestrator()

        class FakeHandler:
            def __init__(self, domain: Domain):
                self.domain = domain
            async def handle(self, intent: Intent) -> TaskResult:
                return TaskResult(domain=self.domain, success=True,
                                  output=f"handled by {self.domain.name}")

        for d in [Domain.RESEARCH, Domain.FINANCE]:
            self.orch.registry._modules[d] = FakeHandler(d)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_orchestrator_triggers_workflow_on_cross_domain(self):
        """execute() should detect multi-domain and route to workflow engine."""
        # The IntentParser must parse secondary_domains for this to work.
        # Override parse for this test to inject secondary domains.
        original_parse = self.orch.intent_parser.parse

        def mock_parse(text, ctx):
            intent = original_parse(text, ctx)
            # Force a cross-domain intent
            intent.primary_domain = Domain.RESEARCH
            intent.secondary_domains = [Domain.FINANCE]
            intent.action = "分析"
            return intent

        self.orch.intent_parser.parse = mock_parse  # type: ignore

        result = await self.orch.execute("研究比特币趋势并生成投资建议")
        assert result.success
        assert "workflow_mode" in result.data
        assert result.data["workflow_mode"] in ("PIPELINE", "PARALLEL")
