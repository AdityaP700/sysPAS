from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from app.grounding.models import SchemaGroundingResult


class CompilationTrace(BaseModel):
    """Logs the step-by-step intermediate code generation, validation, and performance details."""
    step_id: str = Field(
        ...,
        description="Reference identifier of the parsed runbook step"
    )
    generated_spl: Optional[str] = Field(
        default=None,
        description="First-pass generated SPL query"
    )
    optimized_spl: Optional[str] = Field(
        default=None,
        description="Optimized final SPL query"
    )
    validation_results: Dict[str, Any] = Field(
        default_factory=dict,
        description="Outcome dictionary of validation tests (e.g. {'raw': True, 'optimized': True})"
    )
    execution_duration_ms: float = Field(
        ...,
        description="Duration in milliseconds spent executing the compilation pipeline for this step"
    )
    errors: List[str] = Field(
        default_factory=list,
        description="Errors encountered during compilation of this step"
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Warnings encountered during compilation of this step"
    )
    grounding_result: Optional[SchemaGroundingResult] = Field(
        default=None,
        description="Schema grounding mapping analysis payload"
    )
    overall_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Combined overall confidence score across all compilation phases"
    )
    selected_template: Optional[str] = Field(
        default=None,
        description="The SPL template identifier selected for generation"
    )
    grounded_fields: List[str] = Field(
        default_factory=list,
        description="Grounded fields resolved and mapped into template placeholders"
    )
    request_id: Optional[str] = Field(
        default=None,
        description="The request ID associated with the step compilation context"
    )
    correlation_id: Optional[str] = Field(
        default=None,
        description="The correlation ID associated with the step compilation context"
    )
    tenant_id: Optional[str] = Field(
        default=None,
        description="The tenant ID associated with the step compilation context"
    )

