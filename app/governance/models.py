from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class PolicyType(str, Enum):
    EXECUTION = "EXECUTION"
    DEPLOYMENT = "DEPLOYMENT"
    CONNECTOR = "CONNECTOR"
    SECRET = "SECRET"


class PolicyRecord(BaseModel):
    """Pydantic model representing a governance policy version."""
    policy_id: str = Field(..., description="Unique UUID identifying the policy")
    tenant_id: str = Field(..., description="The workspace tenant UUID owner")
    name: str = Field(..., description="Name of the governance policy")
    policy_type: PolicyType = Field(..., description="The scope category of the policy")
    enabled: bool = Field(default=True, description="Active status of this policy")
    priority: int = Field(default=100, description="Conflict resolution priority (higher wins)")
    version: int = Field(default=1, description="Incremental version sequence number")
    is_current: bool = Field(default=True, description="Active version indicator")
    policy_definition: List[Dict[str, Any]] = Field(default_factory=list, description="List of if/then policy rules")
    created_at: str = Field(..., description="Creation ISO timestamp")
    updated_at: str = Field(..., description="Last updated ISO timestamp")


class PolicyDecision(BaseModel):
    """Result of policy engine evaluations."""
    allowed: bool = Field(..., description="Allowed execution outcome")
    matched_policy_id: Optional[str] = Field(None, description="The policy ID that caused the final decision")
    matched_policy_version: Optional[int] = Field(None, description="The version of the matched policy")
    matched_rule: Optional[Dict[str, Any]] = Field(None, description="The specific rule matched within policy_definition")
    violations: List[str] = Field(default_factory=list, description="Explanations of policy violations")
    warnings: List[str] = Field(default_factory=list, description="Warning messages that do not block execution")


class DeploymentRecord(BaseModel):
    """Record of environment promotions and deployments."""
    deployment_id: str
    tenant_id: str
    bundle_id: str
    version: int
    environment: str  # DEV, TEST, STAGING, PRODUCTION
    status: str       # PENDING, APPROVED, REJECTED, SUCCESS, FAILED
    created_at: str


class DeploymentSnapshotRecord(BaseModel):
    """Archived bundle payload snapshot corresponding to a deployment run."""
    snapshot_id: str
    deployment_id: str
    tenant_id: str
    bundle_payload: Dict[str, Any]
    created_at: str


class DeploymentApprovalRecord(BaseModel):
    """Auditable gatekeeper approval for promotions to restricted environments."""
    approval_id: str
    deployment_id: str
    tenant_id: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    decision: str  # PENDING, APPROVED, REJECTED
    comments: Optional[str] = None


class ComplianceSnapshotRecord(BaseModel):
    """Durable compliance report audit snapshot."""
    snapshot_id: str
    tenant_id: str
    report_type: str
    report_data: Dict[str, Any]
    snapshot_hash: str
    created_at: str


class PolicyEventRecord(BaseModel):
    """Auditable logs of policy engine evaluations."""
    event_id: str
    tenant_id: str
    policy_id: Optional[str] = None
    resource_type: str
    resource_id: str
    decision: str  # ALLOW, DENY
    timestamp: str
    expires_at: str
