from typing import List, Dict, Any
from pydantic import BaseModel, Field


class QueryResult(BaseModel):
    """Normalized payload holding raw query output rows and timing metadata."""
    success: bool = Field(..., description="Indicates if search query completed successfully")
    row_count: int = Field(..., description="Number of rows returned")
    rows: List[Dict[str, Any]] = Field(default_factory=list, description="Array of dictionary result maps")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata summary from Splunk")
    duration_ms: float = Field(..., description="Execution time in milliseconds")


class QueryExecutionError(Exception):
    """Raised when execution of a compiled SPL query fails on the Splunk cluster."""
    pass
