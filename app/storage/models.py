from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator


class BundleRecord(BaseModel):
    """Pydantic model representing a compiled and persisted skill bundle."""
    bundle_id: str = Field(..., description="Unique UUID identifying the bundle")
    bundle_name: str = Field(..., description="Name of the compiled runbook")
    version: int = Field(..., description="Incremental version number")
    created_at: str = Field(..., description="Creation ISO 8601 timestamp")
    status: str = Field(..., description="Compilation status of the bundle")
    payload: Dict[str, Any] = Field(..., description="The full SkillBundle JSON dictionary payload")
    tenant_id: str = Field("system", description="The workspace tenant UUID owner of this bundle")
    created_by: str = Field("system", description="The key_id of the bundle creator")
    owner_id: Optional[str] = Field(None, description="Legacy field for backward compatibility")
    environment: str = Field("DEV", description="Target environment for execution")
    promotion_status: str = Field("DRAFT", description="The promotion state of the bundle")

    @model_validator(mode="before")
    @classmethod
    def check_owner(cls, data: any) -> any:
        if isinstance(data, dict):
            owner_id = data.get("owner_id")
            created_by = data.get("created_by")
            if owner_id is not None and created_by is None:
                data["created_by"] = owner_id
            if owner_id is None and created_by is not None:
                data["owner_id"] = created_by
        return data


class CompilationRecord(BaseModel):
    """Pydantic model representing a compilation request and its outcome history."""
    compilation_id: str = Field(..., description="Unique UUID identifying this compilation event")
    bundle_id: str = Field(..., description="Associated bundle UUID")
    timestamp: str = Field(..., description="ISO 8601 timestamp of when compilation occurred")
    duration_ms: float = Field(..., description="Compilation time duration in milliseconds")
    confidence: float = Field(..., description="Average grounding confidence score across steps")
    status: str = Field(..., description="Compilation outcome status")
    tenant_id: str = Field("system", description="The workspace tenant UUID context of compilation")


class TraceRecord(BaseModel):
    """Pydantic model representing an execution trace step compiled for a runbook step."""
    trace_id: str = Field(..., description="Unique UUID identifying this trace record")
    compilation_id: str = Field(..., description="Associated compilation UUID")
    step_id: str = Field(..., description="Parsed step identifier")
    request_id: Optional[str] = Field(None, description="Optional request tracking UUID")
    correlation_id: Optional[str] = Field(None, description="Optional correlation tracking UUID")
    payload: Dict[str, Any] = Field(..., description="The full step CompilationTrace JSON dictionary payload")
    tenant_id: str = Field("system", description="The workspace tenant UUID context of this step trace")
