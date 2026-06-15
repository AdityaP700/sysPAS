from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from app.auth.models import UserRole, GlobalRole, TenantRole


class CreateAPIKeyRequest(BaseModel):
    """Payload representing a request to generate a new API key."""
    name: str = Field(..., description="Human-readable descriptor name of the API key")
    role: Optional[UserRole] = Field(default=None, description="Legacy privilege role associated with the API key")
    tenant_role: Optional[TenantRole] = Field(default=None, description="Workspace privilege role")
    global_role: Optional[GlobalRole] = Field(default=None, description="Global system administration privilege role")


class CreateAPIKeyResponse(BaseModel):
    """Response returned upon key generation. Raw plaintext token is only returned once."""
    key_id: str = Field(..., description="Unique ID of the key record")
    api_key: str = Field(..., description="Plaintext API key token (revealed only once)")
    role: UserRole = Field(..., description="Legacy role assigned to the key")
    tenant_id: str = Field(..., description="Tenant workspace owning the key")
    tenant_role: Optional[TenantRole] = Field(None, description="Workspace role assigned to the key")
    global_role: Optional[GlobalRole] = Field(None, description="Global role assigned to the key")


class APIKeyInfo(BaseModel):
    """Metadata summaries of API keys returned in list requests (no secrets exposed)."""
    key_id: str = Field(..., description="Unique key identifier")
    name: str = Field(..., description="Descriptor name of the key")
    key_prefix: str = Field(..., description="Public revealable key prefix (e.g. rm_key_a1b2)")
    role: UserRole = Field(..., description="Legacy assigned role metadata")
    tenant_id: str = Field(..., description="Tenant workspace owning the key")
    tenant_role: Optional[TenantRole] = Field(None, description="Workspace role assigned to the key")
    global_role: Optional[GlobalRole] = Field(None, description="Global role assigned to the key")
    created_at: str = Field(..., description="Creation ISO 8601 timestamp")
    enabled: bool = Field(..., description="Key active status indicator")


class TenantCreate(BaseModel):
    """Payload to register a new tenant workspace."""
    name: str = Field(..., description="Workspace human readable name")
    slug: str = Field(..., description="Workspace URL slug (must be unique)")


class TenantResponse(BaseModel):
    """Response representing a workspace organization tenant."""
    tenant_id: str = Field(..., description="Unique workspace identifier")
    name: str = Field(..., description="Workspace organization name")
    slug: str = Field(..., description="Unique URL slug")
    created_at: str = Field(..., description="Creation ISO 8601 timestamp")
    enabled: bool = Field(..., description="Active status indicator")


class MembershipCreate(BaseModel):
    """Payload to map an API Key to a tenant workspace membership."""
    api_key_id: str = Field(..., description="The key_id principal to map")
    role: TenantRole = Field(..., description="Role assigned in this workspace")


class MembershipResponse(BaseModel):
    """Response representing a tenant membership mapping."""
    membership_id: str = Field(..., description="Unique membership mapping identifier")
    tenant_id: str = Field(..., description="Workspace tenant identifier")
    api_key_id: str = Field(..., description="Associated API Key ID principal")
    role: TenantRole = Field(..., description="Scoped tenant workspace role")


class TriggerExecutionRequest(BaseModel):
    """Payload to trigger an agent skill execution run."""
    bundle_id: str = Field(..., description="Target compiled skill bundle ID to execute")
    version: Optional[int] = Field(default=None, description="Target version (defaults to latest)")
    input_data: Dict[str, Any] = Field(default_factory=dict, description="Initial input parameters context")


class ResumeExecutionRequest(BaseModel):
    """Payload to decide a paused human-in-the-loop gate approval decision."""
    decision: str = Field(..., description="Decision choice: 'APPROVED' or 'REJECTED'")


class JobStartResponse(BaseModel):
    """Payload returned immediately when triggering an execution asynchronously."""
    job_id: str = Field(..., description="Unique job tracker ID")
    execution_id: str = Field(..., description="Pre-generated execution tracker ID")
    status: str = Field(..., description="Initial job queue status (usually QUEUED)")


class ScheduleCreateRequest(BaseModel):
    """Payload to create a new cron execution schedule."""
    bundle_id: str = Field(..., description="Target compiled skill bundle ID to schedule")
    version: Optional[int] = Field(default=None, description="Target version (defaults to latest)")
    cron_expression: str = Field(..., description="5-field cron string specification")


class ScheduleResponse(BaseModel):
    """Response representing a configured cron schedule."""
    schedule_id: str = Field(..., description="Unique schedule identifier")
    tenant_id: str = Field(..., description="Workspace tenant identifier")
    bundle_id: str = Field(..., description="Target skill bundle ID")
    bundle_version: int = Field(..., description="Target version number")
    cron_expression: str = Field(..., description="5-field cron string")
    enabled: bool = Field(..., description="Schedule active status indicator")
    next_run_at: str = Field(..., description="Calculated next run ISO 8601 timestamp")
    created_by: str = Field(..., description="API key creator ID")
    created_at: str = Field(..., description="Creation ISO 8601 timestamp")
    last_triggered_at: Optional[str] = Field(None, description="Last execution trigger timestamp")


