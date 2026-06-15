import pytest
from app.domain.enums import StepType, CompilationStatus
from app.domain.models import Runbook, RunbookStep
from app.compiler.compiler import RunbookCompiler
from app.spl.generator import MockGenerator
from app.spl.optimizer import MockOptimizer
from app.spl.explainer import MockExplainer
from app.spl.validator import MockValidator
from app.schema.provider import MockSchemaProvider


def test_compiler_success():
    # Setup test runbook
    step1 = RunbookStep(
        step_id="1",
        description="Check auth logs for authentication errors",
        step_type=StepType.DETECTION,
        confidence=0.9,
        data_source="auth_logs"
    )
    step2 = RunbookStep(
        step_id="2",
        description="Correlate threat intel list",
        step_type=StepType.CORRELATION,
        confidence=0.85
    )
    runbook = Runbook(name="Auth Spike Investigation", steps=[step1, step2])

    # Instantiate compiler with mock adapters & schema provider
    compiler = RunbookCompiler(
        generator=MockGenerator(),
        optimizer=MockOptimizer(),
        explainer=MockExplainer(),
        validator=MockValidator(),
        schema_provider=MockSchemaProvider()
    )

    result = compiler.compile(runbook)

    # Asserts
    assert result.status == CompilationStatus.SUCCESS
    assert len(result.steps) == 2
    assert len(result.errors) == 0
    assert len(result.warnings) == 0
    assert len(result.traces) == 2

    # Verify generated raw and optimized queries
    assert result.steps[0].raw_spl == "index=auth_logs action=failure | stats count by user, src_ip"
    assert result.steps[0].compiled_spl == "index=auth_logs action=failure | stats count by user, src_ip earliest=-15m"
    assert "index=auth_logs action=failure | stats count by user, src_ip earliest=-15m" in result.steps[0].explanation
    assert result.steps[0].status == CompilationStatus.SUCCESS

    # Verify traces are populated
    assert result.traces[0].step_id == "1"
    assert result.traces[0].generated_spl == "index=auth_logs action=failure | stats count by user, src_ip"
    assert result.traces[0].optimized_spl == "index=auth_logs action=failure | stats count by user, src_ip earliest=-15m"
    assert result.traces[0].validation_results == {"raw_valid": True, "optimized_valid": True}
    assert result.traces[0].execution_duration_ms >= 0.0


def test_compiler_warnings_collected():
    step1 = RunbookStep(
        step_id="1",
        description="Check logs manually",
        step_type=StepType.MANUAL,
        confidence=0.9
    )
    step2 = RunbookStep(
        step_id="2",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.5
    )
    runbook = Runbook(name="Low Confidence Runbook", steps=[step1, step2])

    compiler = RunbookCompiler(
        generator=MockGenerator(),
        optimizer=MockOptimizer(),
        explainer=MockExplainer(),
        validator=MockValidator(),
        schema_provider=MockSchemaProvider()
    )

    result = compiler.compile(runbook)

    assert result.status == CompilationStatus.SUCCESS
    assert len(result.warnings) == 2
    
    warning_codes = [w.code for w in result.warnings]
    assert "WRN_MANUAL_STEP" in warning_codes
    assert "WRN_LOW_CONFIDENCE" in warning_codes

    # Verify warnings are recorded in traces
    assert any("MANUAL" in w for w in result.traces[0].warnings)
    assert any("low confidence" in w for w in result.traces[1].warnings)


def test_compiler_failed_raw_validation():
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    runbook = Runbook(name="Failing Runbook", steps=[step])

    validator = MockValidator(invalid_queries={"index=main action=failure | stats count by user, src_ip"})
    compiler = RunbookCompiler(
        generator=MockGenerator(),
        optimizer=MockOptimizer(),
        explainer=MockExplainer(),
        validator=validator,
        schema_provider=MockSchemaProvider()
    )

    result = compiler.compile(runbook)

    assert result.status == CompilationStatus.FAILED
    assert len(result.steps) == 1
    assert result.steps[0].status == CompilationStatus.FAILED
    assert len(result.errors) == 1
    assert result.errors[0].code == "ERR_VAL_RAW"
    
    # Verify errors are recorded in traces
    assert len(result.traces[0].errors) == 1
    assert "failed validation" in result.traces[0].errors[0]


def test_compiler_failed_optimized_validation():
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    runbook = Runbook(name="Failing Runbook", steps=[step])

    validator = MockValidator(invalid_queries={"index=main action=failure | stats count by user, src_ip earliest=-15m"})
    compiler = RunbookCompiler(
        generator=MockGenerator(),
        optimizer=MockOptimizer(),
        explainer=MockExplainer(),
        validator=validator,
        schema_provider=MockSchemaProvider()
    )

    result = compiler.compile(runbook)

    assert result.status == CompilationStatus.FAILED
    assert len(result.steps) == 1
    assert result.steps[0].status == CompilationStatus.FAILED
    assert len(result.errors) == 1
    assert result.errors[0].code == "ERR_VAL_OPT"


def test_compiler_partial_status():
    step1 = RunbookStep(
        step_id="1",
        description="Check auth logs",  # fails raw validation
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    step2 = RunbookStep(
        step_id="2",
        description="Generate standard query",  # succeeds
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    runbook = Runbook(name="Partial Success Runbook", steps=[step1, step2])

    validator = MockValidator(invalid_queries={"index=main action=failure | stats count by user, src_ip"})
    compiler = RunbookCompiler(
        generator=MockGenerator(),
        optimizer=MockOptimizer(),
        explainer=MockExplainer(),
        validator=validator,
        schema_provider=MockSchemaProvider()
    )

    result = compiler.compile(runbook)

    assert result.status == CompilationStatus.PARTIAL
    assert len(result.steps) == 2
    assert result.steps[0].status == CompilationStatus.FAILED
    assert result.steps[1].status == CompilationStatus.SUCCESS
    assert len(result.errors) == 1


def test_compiler_schema_integration():
    """Verifies compiler resolves schema fields and injects them into GenerationContext."""
    step = RunbookStep(
        step_id="1",
        description="Search failed transactions in sales database",
        step_type=StepType.DETECTION,
        data_source="sales_metrics",
        confidence=0.9
    )
    runbook = Runbook(name="Schema Context Runbook", steps=[step])

    # A custom generator that inspects schema fields from the context
    class ContextInspectingGenerator(MockGenerator):
        def generate(self, context):
            assert "store_id" in context.schema_fields
            assert "revenue_drop_pct" in context.schema_fields
            assert context.data_source == "sales_metrics"
            return "index=sales"

    compiler = RunbookCompiler(
        generator=ContextInspectingGenerator(),
        optimizer=MockOptimizer(),
        explainer=MockExplainer(),
        validator=MockValidator(),
        schema_provider=MockSchemaProvider()
    )

    result = compiler.compile(runbook)
    assert result.status == CompilationStatus.SUCCESS
    assert result.steps[0].raw_spl == "index=sales"
