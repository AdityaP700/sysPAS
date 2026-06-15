from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class AuditEventRecord(BaseModel):
    """Pydantic model representing a single structured audit log entry."""
    audit_id: str = Field(..., description="Unique UUID identifying this audit log")
    timestamp: str = Field(..., description="ISO 8601 creation timestamp")
    request_id: Optional[str] = Field(None, description="Request trace UUID")
    correlation_id: Optional[str] = Field(None, description="Correlation trace UUID")
    user_id: Optional[str] = Field(None, description="Authenticated key identifier")
    role: Optional[str] = Field(None, description="Privilege role of caller")
    action: str = Field(..., description="The state-changing operation executed (e.g. COMPILE_RUNBOOK)")
    resource_type: str = Field(..., description="Targeted resource type name (e.g. bundle)")
    resource_id: Optional[str] = Field(None, description="Identifier of the target resource")
    status: str = Field(..., description="Outcome of the action (e.g. SUCCESS, FAILED)")
    details: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary execution metadata details")
    tenant_id: str = Field("system", description="The workspace tenant UUID owner of this audit event")
