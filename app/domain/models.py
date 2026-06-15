from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
from app.domain.enums import StepType, ActionType, ApprovalLevel, CompilationStatus
from app.diagnostics.models import CompilationWarning, CompilationError
from app.tracing.models import CompilationTrace
from app.agent.graph import ExecutionGraph
from app.agent.governance import GovernancePolicy


class RunbookStep(BaseModel):
    """Represents a single step parsed from a runbook/SOP."""
    step_id: str = Field(
        ...,
        description="Unique identifier for the step (can be sequential or hierarchical, e.g., '1', '1.a')"
    )
    description: str = Field(
        ...,
        description="The natural language description of what the step does"
    )
    step_type: StepType = Field(
        default=StepType.INVESTIGATION,
        description="The operational classification of this step"
    )
    action: Optional[str] = Field(
        default=None,
        description="The specific action details associated with this step, if any"
    )
    data_source: Optional[str] = Field(
        default=None,
        description="The target data source/index or indices involved in this step"
    )
    condition: Optional[str] = Field(
        default=None,
        description="Logical condition or rule to be checked"
    )
    threshold: Optional[str] = Field(
        default=None,
        description="Quantitative threshold for matching/alerting"
    )
    time_window: Optional[str] = Field(
        default=None,
        description="Time constraint context for the search (e.g., '5m', '1h')"
    )
    join_required: bool = Field(
        default=False,
        description="Flag indicating if a cross-source/federated join is required"
    )
    snowflake_table: Optional[str] = Field(
        default=None,
        description="Snowflake table name if cross-source federated search is needed"
    )
    gate: Optional[str] = Field(
        default=None,
        description="Human-in-the-loop gate classification"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="The parser's decomposition confidence score for this step"
    )


class Runbook(BaseModel):
    """Represents a fully parsed runbook/SOP structure."""
    name: str = Field(
        ...,
        description="The name/title of the runbook"
    )
    description: Optional[str] = Field(
        default=None,
        description="A high-level description of the runbook's objective"
    )
    steps: List[RunbookStep] = Field(
        default_factory=list,
        description="Ordered list of Runbook steps"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary metadata container for parsed properties"
    )


class CompiledStep(BaseModel):
    """Represents a runbook step that has undergone SPL compilation."""
    step_id: str = Field(
        ...,
        description="Reference to the original step_id"
    )
    description: str = Field(
        ...,
        description="Description of the step being compiled"
    )
    raw_spl: Optional[str] = Field(
        default=None,
        description="Initial generated SPL query"
    )
    compiled_spl: Optional[str] = Field(
        default=None,
        description="Optimized, execution-ready SPL query"
    )
    explanation: Optional[str] = Field(
        default=None,
        description="Human-readable explanation of the query logic"
    )
    status: CompilationStatus = Field(
        default=CompilationStatus.PENDING,
        description="Compilation status of this step"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="The compiler's generation/optimization confidence score"
    )


class CompilationResult(BaseModel):
    """Represents the end result of compiling an entire runbook."""
    runbook_name: str = Field(
        ...,
        description="Name of the runbook compiled"
    )
    steps: List[CompiledStep] = Field(
        default_factory=list,
        description="List of compiled steps"
    )
    status: CompilationStatus = Field(
        ...,
        description="Overall status of the runbook compilation"
    )
    errors: List[CompilationError] = Field(
        default_factory=list,
        description="Diagnostics errors collected during compilation"
    )
    warnings: List[CompilationWarning] = Field(
        default_factory=list,
        description="Diagnostics warnings collected during compilation"
    )
    traces: List[CompilationTrace] = Field(
        default_factory=list,
        description="Traces collected during compilation of individual steps"
    )


class AgentSkill(BaseModel):
    """Represents the compiled skill JSON structure ready for Cisco AI Toolkit Agent Builder deployment."""
    name: str = Field(
        ...,
        description="Name of the skill"
    )
    source_runbook: str = Field(
        ...,
        description="Source runbook identifier or filename"
    )
    compiler_version: str = Field(
        default="1.0.0",
        description="Version of the compiler used to build this skill"
    )
    graph: ExecutionGraph = Field(
        ...,
        description="Graph detailing steps nodes and logic flow routing edges"
    )
    governance: GovernancePolicy = Field(
        ...,
        description="Governance policy governing execution authorization"
    )
    steps: List[CompiledStep] = Field(
        default_factory=list,
        description="Execution-ready steps forming the agent's workflow"
    )


class ValidationResult(BaseModel):
    """Represents the results of runbook structure and semantic validation."""
    is_valid: bool = Field(
        ...,
        description="Indicates whether the runbook passed all validation rules"
    )
    errors: List[str] = Field(
        default_factory=list,
        description="List of error messages detail validation failures"
    )
