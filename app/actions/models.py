from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ActionResult(BaseModel):
    """The runtime validation and outcome returned by executing an action connector."""
    success: bool = Field(..., description="Execution status boolean")
    action_type: str = Field(..., description="Action connector name")
    external_id: Optional[str] = Field(default=None, description="External identifier, if any, returned by the system")
    details: Dict[str, Any] = Field(default_factory=dict, description="Metadata key-values returned from target endpoint")
    duration_ms: float = Field(..., description="Time taken to execute in milliseconds")
