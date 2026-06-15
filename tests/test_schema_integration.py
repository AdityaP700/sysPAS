import pytest
from unittest.mock import patch, MagicMock
from app.compiler.compiler import RunbookCompiler
from app.domain.models import Runbook, RunbookStep
from app.domain.enums import StepType, CompilationStatus
from app.schema.discovery import SchemaDiscoveryEngine
from app.schema.cache import SchemaCache
from app.spl.generator import MockGenerator
from app.spl.optimizer import MockOptimizer
from app.spl.explainer import MockExplainer
from app.spl.validator import MockValidator


def test_schema_discovery_compiler_integration():
    """Verify end-to-end compilation using real SchemaDiscoveryEngine and FieldIntelligenceEngine."""
    # 1. Setup step
    step = RunbookStep(
        step_id="1",
        description="Check auth logs for user_name and client ip",
        step_type=StepType.DETECTION,
        data_source="auth_logs",
        confidence=1.0
    )
    runbook = Runbook(name="Integration Test Runbook", steps=[step])

    # 2. Setup discovery engine with a fresh cache
    cache = SchemaCache()
    schema_provider = SchemaDiscoveryEngine(cache=cache)

    # 3. Instantiate compiler
    compiler = RunbookCompiler(
        generator=MockGenerator(),
        optimizer=MockOptimizer(),
        explainer=MockExplainer(),
        validator=MockValidator(),
        schema_provider=schema_provider
    )

    # Mock Splunk MCP response for auth_logs
    mock_fields_response = '{"fields": ["src_ip", "user", "action", "status"]}'

    with patch("app.schema.discovery.call_mcp_tool", return_value=mock_fields_response) as mock_mcp:
        result = compiler.compile(runbook)

        assert result.status == CompilationStatus.SUCCESS
        assert len(result.steps) == 1
        
        # Verify generated SPL uses resolved canonical fields
        # "user_name" -> resolves to "user" (synonym, weight 0.9)
        # "client ip" -> resolves to "src_ip" (synonym, weight 0.9)
        compiled_step = result.steps[0]
        assert compiled_step.raw_spl == "index=auth_logs action=failure | stats count by user, src_ip"
        assert compiled_step.compiled_spl == "index=auth_logs action=failure | stats count by user, src_ip earliest=-15m"

        # Verify grounding trace records the resolved fields
        trace = result.traces[0]
        assert set(trace.grounding_result.resolved_fields) == {"src_ip", "user"}
        assert trace.grounding_result.confidence == 0.95

        # Verify average confidence calculated correctly
        # parser_conf = 1.0
        # grounding_conf = 0.95
        # generator_conf = 1.0 (no penalty)
        # overall = round(1.0 * 0.95 * 1.0, 2) = 0.95
        assert compiled_step.confidence == 0.95

        # 4. Verify cache hit on second compile (should not invoke MCP tool again)
        mock_mcp.reset_mock()
        result_cached = compiler.compile(runbook)
        
        assert result_cached.status == CompilationStatus.SUCCESS
        assert result_cached.steps[0].compiled_spl == compiled_step.compiled_spl
        mock_mcp.assert_not_called()
