from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ConnectorType(str, Enum):
    SLACK = "SLACK"
    MICROSOFT_TEAMS = "MICROSOFT_TEAMS"
    JIRA = "JIRA"
    SERVICENOW = "SERVICENOW"
    PAGERDUTY = "PAGERDUTY"
    EMAIL = "EMAIL"


class ConnectorRecord(BaseModel):
    connector_id: str = Field(..., description="Unique connector identifier")
    tenant_id: str = Field(..., description="Workspace boundary grouping")
    connector_type: ConnectorType = Field(..., description="Operational connector type")
    name: str = Field(..., description="Friendly connector name")
    description: Optional[str] = Field(default=None, description="Detailed connector description")
    enabled: bool = Field(default=True, description="Active status of this connector")
    configuration: Dict[str, Any] = Field(default_factory=dict, description="Configuration parameters dictionary")
    connector_version: int = Field(default=1, description="Version of the connector configuration")
    schema_version: int = Field(default=1, description="Schema version of this connector type")
    health_status: str = Field(default="UNKNOWN", description="Connector operational health status")
    last_health_check: Optional[str] = Field(default=None, description="ISO timestamp of last health check")
    last_success_at: Optional[str] = Field(default=None, description="ISO timestamp of last successful check")
    consecutive_failures: int = Field(default=0, description="Consecutive health check failure count")
    last_validation_at: Optional[str] = Field(default=None, description="ISO timestamp of last credential validation")
    validation_error: Optional[str] = Field(default=None, description="Error message of last validation failure")
    rate_limit_per_minute: int = Field(default=100, description="Rate limit per minute")
    circuit_state: str = Field(default="CLOSED", description="Circuit breaker state: CLOSED, OPEN, HALF_OPEN")
    circuit_failure_count: int = Field(default=0, description="Consecutive execution failure count")
    circuit_opened_at: Optional[str] = Field(default=None, description="ISO timestamp when circuit was opened")
    created_at: str = Field(..., description="ISO created timestamp")
    updated_at: str = Field(..., description="ISO updated timestamp")
    environment: str = Field(default="DEV", description="Target environment boundary")
