from app.domain.models import RunbookStep
from app.context.generation_context import GenerationContext
from app.domain.enums import StepType


def test_generation_context_construction():
    step = RunbookStep(
        step_id="1",
        description="Check logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    
    context = GenerationContext(
        step=step,
        schema_fields=["src_ip", "user"],
        data_source="auth_logs",
        constraints={"time_window": "5m"},
        metadata={"run_id": "xyz"}
    )
    
    assert context.step.step_id == "1"
    assert context.schema_fields == ["src_ip", "user"]
    assert context.data_source == "auth_logs"
    assert context.constraints["time_window"] == "5m"
    assert context.metadata["run_id"] == "xyz"
    
    # Verify serialization
    dump = context.model_dump()
    assert dump["step"]["step_id"] == "1"
    assert dump["schema_fields"] == ["src_ip", "user"]
    assert dump["data_source"] == "auth_logs"
