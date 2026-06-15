from typing import Optional
from pydantic import BaseModel, Field


class BranchCondition(BaseModel):
    """Represents a specific trigger condition expression used for branching path guards."""
    expression: str = Field(
        ...,
        description="The natural language or query expression (e.g., 'failures > 100', 'source_ip == internal')"
    )
    operator: Optional[str] = Field(
        default=None,
        description="Optional parsed logical/mathematical operator (e.g. '==', '!=', '>', '<')"
    )
    value: Optional[str] = Field(
        default=None,
        description="Optional parsed comparison value"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="The planner's parsing confidence score"
    )
