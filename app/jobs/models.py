from enum import Enum
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobRecord(BaseModel):
    job_id: str = Field(..., description="Unique job instance ID")
    tenant_id: str = Field(..., description="Workspace organization boundary")
    execution_id: str = Field(..., description="Associated workflow run execution ID")
    bundle_id: str = Field(..., description="Target skill bundle ID")
    bundle_version: int = Field(..., description="Target skill version")
    status: JobStatus = Field(default=JobStatus.QUEUED, description="Background job state")
    attempt_count: int = Field(default=0, description="Current execution attempt number")
    max_attempts: int = Field(default=3, description="Maximum execution attempts allowed")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    started_at: Optional[str] = Field(default=None, description="ISO 8601 start timestamp")
    completed_at: Optional[str] = Field(default=None, description="ISO 8601 completion timestamp")
    last_error: Optional[str] = Field(default=None, description="Error details if execution failed")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Execution input context variables")
    run_at: Optional[str] = Field(default=None, description="ISO 8601 schedule run time (durable retry delay)")
    created_by: str = Field(..., description="Identity of the API Key that scheduled/triggered the job")
    worker_id: Optional[str] = Field(default=None, description="Identifier of the BackgroundWorker processing the job")
    priority: int = Field(default=100, description="Priority weight (lower runs first)")
    schedule_fire_id: Optional[str] = Field(default=None, description="De-duplication scheduled run identifier")


class ScheduleRecord(BaseModel):
    schedule_id: str = Field(..., description="Unique cron schedule ID")
    tenant_id: str = Field(..., description="Workspace organization boundary")
    bundle_id: str = Field(..., description="Target skill bundle ID")
    bundle_version: int = Field(..., description="Target skill version")
    cron_expression: str = Field(..., description="5-field cron string specification")
    enabled: bool = Field(default=True, description="Enables or suspends cron evaluations")
    next_run_at: str = Field(..., description="ISO 8601 timestamp for the next calculated run")
    created_by: str = Field(..., description="Identity of the API Key that created the schedule")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    last_triggered_at: Optional[str] = Field(default=None, description="ISO 8601 timestamp of the last triggered job")
