from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class SecretType(str, Enum):
    API_KEY = "API_KEY"
    PASSWORD = "PASSWORD"
    TOKEN = "TOKEN"
    WEBHOOK = "WEBHOOK"
    CERTIFICATE = "CERTIFICATE"
    GENERIC = "GENERIC"


class SecretRecord(BaseModel):
    secret_id: str = Field(..., description="Unique secret ID")
    tenant_id: str = Field(..., description="Workspace boundary grouping")
    name: str = Field(..., description="Secret identifier name")
    secret_type: SecretType = Field(..., description="The type/category of the credential")
    encrypted_value: str = Field(..., description="Base64 encoded encrypted string")
    version: int = Field(..., description="The version sequence number")
    enabled: bool = Field(default=True, description="Active status of this version")
    is_current: bool = Field(default=True, description="Whether this is the currently active version")
    created_at: str = Field(..., description="ISO 8601 created timestamp")
    updated_at: str = Field(..., description="ISO 8601 updated timestamp")
    environment: str = Field(default="DEV", description="Target environment boundary")
