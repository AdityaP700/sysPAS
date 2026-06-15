from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class UserRole(str, Enum):
    """Legacy UserRole enum mapped for backward compatibility."""
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class GlobalRole(str, Enum):
    """System-level global role defining broad application administrative privileges."""
    ADMIN = "ADMIN"


class TenantRole(str, Enum):
    """Tenant-level workspace roles defining scoped tenant boundaries privileges."""
    TENANT_ADMIN = "TENANT_ADMIN"
    TENANT_OPERATOR = "TENANT_OPERATOR"
    TENANT_VIEWER = "TENANT_VIEWER"


class TenantRecord(BaseModel):
    """Represents a workspace organization tenant inside the database storage."""
    tenant_id: str = Field(..., description="Unique workspace organization identifier")
    name: str = Field(..., description="Human-readable workspace name")
    slug: str = Field(..., description="Unique URL slug constraint (e.g. soc-team)")
    created_at: str = Field(..., description="ISO 8601 timestamp of registration")
    enabled: bool = Field(True, description="Active status indicator of workspace")
    deleted_at: Optional[str] = Field(None, description="ISO 8601 timestamp of soft-deletion")


class MembershipRecord(BaseModel):
    """Represents a member mapping associating API Key principals to a Tenant workspace."""
    membership_id: str = Field(..., description="Unique identifier of the membership")
    tenant_id: str = Field(..., description="Target workspace tenant identifier")
    api_key_id: str = Field(..., description="Associated API key identity principal identifier")
    role: TenantRole = Field(..., description="Assigned tenant role scoping permission boundaries")


class APIKeyRecord(BaseModel):
    """Represents a persisted API key entry inside the database storage."""
    key_id: str = Field(..., description="Unique identifier of the API key")
    name: str = Field(..., description="Human-readable descriptor name")
    key_hash: str = Field(..., description="SHA-256 hash representation of token")
    key_prefix: str = Field(..., description="Revealable preview prefix for listings (e.g. rm_key_a1b2)")
    tenant_id: str = Field("system", description="The workspace tenant UUID owner of this API Key")
    global_role: Optional[GlobalRole] = Field(None, description="Assigned global system-wide role")
    tenant_role: Optional[TenantRole] = Field(None, description="Assigned workspace role")
    created_at: str = Field(..., description="ISO 8601 timestamp of creation")
    enabled: bool = Field(True, description="Active status indicator of the key")
    role: Optional[UserRole] = Field(None, description="Legacy user role for backward compatibility")

    @model_validator(mode="before")
    @classmethod
    def check_roles(cls, data: any) -> any:
        if isinstance(data, dict):
            role = data.get("role")
            global_role = data.get("global_role")
            tenant_role = data.get("tenant_role")
            
            # If role is passed (legacy), map it to global_role or tenant_role
            if role is not None and global_role is None and tenant_role is None:
                if role == UserRole.ADMIN or role == "ADMIN":
                    data["global_role"] = GlobalRole.ADMIN
                    data["tenant_role"] = TenantRole.TENANT_ADMIN
                elif role == UserRole.OPERATOR or role == "OPERATOR":
                    data["tenant_role"] = TenantRole.TENANT_OPERATOR
                else:
                    data["tenant_role"] = TenantRole.TENANT_VIEWER
            
            # If global_role or tenant_role is passed, map it to legacy role
            if role is None:
                if global_role == GlobalRole.ADMIN:
                    data["role"] = UserRole.ADMIN
                elif tenant_role == TenantRole.TENANT_ADMIN:
                    data["role"] = UserRole.ADMIN
                elif tenant_role == TenantRole.TENANT_OPERATOR:
                    data["role"] = UserRole.OPERATOR
                else:
                    data["role"] = UserRole.VIEWER
        return data


class AuthenticatedUser(BaseModel):
    """Current caller security principal context injected into Request scopes."""
    user_id: str = Field(..., description="Assigned unique user/key identifier")
    tenant_id: str = Field("system", description="Workspace tenant UUID owner of caller")
    global_role: Optional[GlobalRole] = Field(None, description="Caller system-wide privilege level")
    tenant_role: Optional[TenantRole] = Field(None, description="Caller workspace privilege level")
    name: str = Field(..., description="Name associated with principal key")
    role: Optional[UserRole] = Field(None, description="Legacy user role for backward compatibility")

    @model_validator(mode="before")
    @classmethod
    def check_roles(cls, data: any) -> any:
        if isinstance(data, dict):
            role = data.get("role")
            global_role = data.get("global_role")
            tenant_role = data.get("tenant_role")
            
            # If role is passed (legacy), map it to global_role or tenant_role
            if role is not None and global_role is None and tenant_role is None:
                if role == UserRole.ADMIN or role == "ADMIN":
                    data["global_role"] = GlobalRole.ADMIN
                    data["tenant_role"] = TenantRole.TENANT_ADMIN
                elif role == UserRole.OPERATOR or role == "OPERATOR":
                    data["tenant_role"] = TenantRole.TENANT_OPERATOR
                else:
                    data["tenant_role"] = TenantRole.TENANT_VIEWER
            
            # If global_role or tenant_role is passed, map it to legacy role
            if role is None:
                if global_role == GlobalRole.ADMIN:
                    data["role"] = UserRole.ADMIN
                elif tenant_role == TenantRole.TENANT_ADMIN:
                    data["role"] = UserRole.ADMIN
                elif tenant_role == TenantRole.TENANT_OPERATOR:
                    data["role"] = UserRole.OPERATOR
                else:
                    data["role"] = UserRole.VIEWER
        return data
