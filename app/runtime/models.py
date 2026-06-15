from enum import Enum
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class FailureCategory(str, Enum):
    QUERY_ERROR = "QUERY_ERROR"
    ACTION_ERROR = "ACTION_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    POLICY_VIOLATION = "POLICY_VIOLATION"


class ExecutionRecord(BaseModel):
    execution_id: str = Field(..., description="Unique workflow run ID")
    tenant_id: str = Field(..., description="Workspace boundary grouping")
    bundle_id: str = Field(..., description="Target skill bundle ID")
    bundle_version: int = Field(..., description="Target skill version")
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING, description="Workflow execution status")
    current_node_id: Optional[str] = Field(default=None, description="Cursor referencing active execution node")
    started_at: str = Field(..., description="ISO timestamps")
    completed_at: Optional[str] = Field(default=None, description="ISO timestamps")
    triggered_by: str = Field(..., description="API Key ID triggering execution")
    context_payload: Dict[str, Any] = Field(default_factory=dict, description="Variables context map")
    failure_category: Optional[FailureCategory] = Field(default=None, description="Workflow run exit categorization")


class NodeExecutionRecord(BaseModel):
    node_execution_id: str = Field(..., description="Unique node run instance ID")
    execution_id: str = Field(...)
    node_id: str = Field(...)
    status: ExecutionStatus = Field(...)
    started_at: str = Field(...)
    completed_at: Optional[str] = Field(default=None)
    input_data: Dict[str, Any] = Field(default_factory=dict)
    output_data: Dict[str, Any] = Field(default_factory=dict)


class ApprovalRecord(BaseModel):
    approval_id: str = Field(..., description="Approval record ID")
    execution_id: str = Field(...)
    node_id: str = Field(...)
    requested_at: str = Field(...)
    decided_at: Optional[str] = Field(default=None)
    decision: Optional[ApprovalStatus] = Field(default=None)
    decided_by: Optional[str] = Field(default=None)


class ActionExecutionRecord(BaseModel):
    action_execution_id: str = Field(..., description="Unique action run instance ID")
    tenant_id: str = Field(..., description="Workspace boundary grouping")
    execution_id: str = Field(...)
    node_id: str = Field(...)
    action_type: str = Field(...)
    external_id: Optional[str] = Field(default=None)
    success: bool = Field(...)
    duration_ms: float = Field(...)
    payload: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(...)
    created_at: str = Field(...)


class InvestigationResult(BaseModel):
    spl: str
    result_count: int
    sample_results: list

