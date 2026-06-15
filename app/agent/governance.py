from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    """Execution mode of the agent skill workflow."""
    AUTO = "AUTO"
    HUMAN_IN_LOOP = "HUMAN_IN_LOOP"
    MANUAL = "MANUAL"


class GovernancePolicy(BaseModel):
    """Defines the authorization requirements, roles, and auditing for agent execution."""
    approval_required: bool = Field(
        ...,
        description="Indicates if explicit human approval is required before execution"
    )
    approval_role: Optional[str] = Field(
        default=None,
        description="The RBAC role required to authorize execution (e.g. 'soc_analyst', 'admin')"
    )
    audit_enabled: bool = Field(
        default=True,
        description="Indicates if execution activities must be recorded in audit logs"
    )
    execution_mode: ExecutionMode = Field(
        ...,
        description="Operational execution mode for the skill"
    )
