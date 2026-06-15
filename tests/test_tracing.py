import pytest
from app.tracing.models import CompilationTrace
from app.context.generation_context import GenerationContext
from app.domain.models import RunbookStep
from app.domain.enums import StepType
from app.splunk.adapters.mcp_generator import SplunkMCPGenerator
from app.splunk.adapters.mcp_optimizer import SplunkMCPOptimizer
from app.splunk.adapters.mcp_explainer import SplunkMCPExplainer
from app.splunk.adapters.mcp_validator import SplunkMCPValidator


from app.splunk.adapters.client import SplunkMCPAdapterError


def test_compilation_trace_properties():
    trace = CompilationTrace(
        step_id="1",
        generated_spl="index=auth",
        optimized_spl="index=auth earliest=-15m",
        validation_results={"raw": True, "optimized": True},
        execution_duration_ms=12.5,
        errors=["Raw validation failed"],
        warnings=["Low confidence"]
    )
    
    assert trace.step_id == "1"
    assert trace.generated_spl == "index=auth"
    assert trace.optimized_spl == "index=auth earliest=-15m"
    assert trace.validation_results == {"raw": True, "optimized": True}
    assert trace.execution_duration_ms == 12.5
    assert trace.errors == ["Raw validation failed"]
    assert trace.warnings == ["Low confidence"]


def test_splunk_adapters_contract():
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION
    )
    context = GenerationContext(step=step)

    generator = SplunkMCPGenerator()
    optimizer = SplunkMCPOptimizer()
    explainer = SplunkMCPExplainer()
    validator = SplunkMCPValidator()

    # Verify they throw SplunkMCPAdapterError
    with pytest.raises(SplunkMCPAdapterError):
        generator.generate(context)

    with pytest.raises(SplunkMCPAdapterError):
        optimizer.optimize("query", context)

    with pytest.raises(SplunkMCPAdapterError):
        explainer.explain("query", context)

    with pytest.raises(SplunkMCPAdapterError):
        validator.validate("query", context)
