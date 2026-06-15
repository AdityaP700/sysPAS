from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from app.domain.models import RunbookStep


class GenerationContext(BaseModel):
    """Holds operational, schema, and structural constraints during compilation."""
    step: RunbookStep = Field(
        ...,
        description="The source RunbookStep model being compiled"
    )
    schema_fields: List[str] = Field(
        default_factory=list,
        description="The available fields in the target data source schema"
    )
    data_source: Optional[str] = Field(
        default=None,
        description="Resolved data source index name"
    )
    constraints: Dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value constraints like time windows, threshold limits"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional custom compilation metadata"
    )
