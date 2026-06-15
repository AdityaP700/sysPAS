from typing import List
from pydantic import BaseModel, Field


class SchemaGroundingResult(BaseModel):
    """Contains outcome mapping details of resolving runbook step fields against schemas."""
    requested_fields: List[str] = Field(
        default_factory=list,
        description="Fields identified from the step description"
    )
    resolved_fields: List[str] = Field(
        default_factory=list,
        description="Fields resolved and matched against schema names"
    )
    missing_fields: List[str] = Field(
        default_factory=list,
        description="Requested fields that were not found in the actual schema"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Grounding phase confidence score based on field resolution status"
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Warning messages collected during grounding checks"
    )
