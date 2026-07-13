"""End-to-end workflow smoke test — verifies cross-domain pipeline with real LLM handlers."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jarvis.core.orchestrator import Orchestrator, Domain, Intent
from jarvis.core.llm import get_llm

# Load all domain modules (each is a sub-package with DomainModule class)
from jarvis.domains.research import DomainModule as ResearchModule
from jarvis.domains.engineering import DomainModule as EngineeringModule
from jarvis.domains.creator import DomainModule as CreatorModule
from jarvis.domains.personal import DomainModule as PersonalModule
from jarvis.domains.security import DomainModule as SecurityModule
from jarvis.domains.health import DomainModule as HealthModule
from jarvis.domains.finance import DomainModule as FinanceModule
from jarvis.domains.home import DomainModule as HomeModule


async def main():
    # Init LLM in mock mode (default)
    get_llm()

    # Build orchestrator (optional deps = None for mock mode)
    orch = Orchestrator()

    # Register all domains
    orch.registry.register(Domain.RESEARCH, ResearchModule())
    orch.registry.register(Domain.FINANCE, FinanceModule())
    orch.registry.register(Domain.ENGINEERING, EngineeringModule())
    orch.registry.register(Domain.CREATOR, CreatorModule())
    orch.registry.register(Domain.PERSONAL, PersonalModule())
    orch.registry.register(Domain.SECURITY, SecurityModule())
    orch.registry.register(Domain.HEALTH, HealthModule())
    orch.registry.register(Domain.HOME, HomeModule())

    print("=" * 60)
    print("TEST 1: Single-domain (no workflow)")
    print("=" * 60)
    result = await orch.execute("hello")
    print(f"  Domain: {result.domain.name}")
    print(f"  Success: {result.success}")
    print(f"  Output (first 100 chars): {str(result.output)[:100]}")
    assert "workflow_mode" not in result.data, "Single-domain should not trigger workflow"
    print("  PASS: No workflow triggered\n")

    print("=" * 60)
    print("TEST 2: Cross-domain pipeline (Research → Finance)")
    print("=" * 60)
    # Force a cross-domain intent
    intent = Intent(
        raw_text="研究比特币最新趋势，然后生成投资建议",
        primary_domain=Domain.RESEARCH,
        secondary_domains=[Domain.FINANCE],
        action="分析",
    )
    wf_result = await orch.workflow.execute(intent)
    print(f"  Success: {wf_result.success}")
    print(f"  Mode: {wf_result.mode.name}")
    print(f"  Steps: {len(wf_result.steps)}")
    for i, (domain, tr) in enumerate(wf_result.steps):
        print(f"    Step {i+1}: {domain.name} → success={tr.success}")
    print(f"  Merged output (first 200 chars):\n    {wf_result.merged_output[:200]}")
    print(f"  Execution time: {wf_result.total_execution_ms:.0f}ms")
    assert wf_result.all_succeeded, "All steps should succeed"
    assert len(wf_result.steps) == 2
    print("  PASS: Pipeline executed\n")

    print("=" * 60)
    print("TEST 3: Cross-domain via Orchestrator.execute()")
    print("=" * 60)
    # Override intent parser to simulate multi-domain
    original_parse = orch.intent_parser.parse
    orch.intent_parser.parse = lambda text, ctx: Intent(
        raw_text=text,
        primary_domain=Domain.RESEARCH,
        secondary_domains=[Domain.ENGINEERING, Domain.CREATOR],
        action="分析",
    )

    result = await orch.execute("调研量子计算，生成技术文档和代码示例")
    print(f"  Success: {result.success}")
    print(f"  workflow_mode: {result.data.get('workflow_mode')}")
    print(f"  steps: {result.data.get('steps')}")
    print(f"  Output (first 300 chars):\n    {str(result.output)[:300]}")
    assert result.success
    assert "workflow_mode" in result.data

    # Restore
    orch.intent_parser.parse = original_parse
    print("  PASS: Orchestrator routes to workflow\n")

    print("=" * 60)
    print("ALL SMOKE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
