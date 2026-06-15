from typing import List
from pydantic import BaseModel, Field


class SchemaContext(BaseModel):
    """Holds fields and properties of a resolved data source index."""
    data_source: str = Field(..., description="Target data source identifier")
    fields: List[str] = Field(default_factory=list, description="Fields available under the schema")


class SchemaDiscoveryResult(BaseModel):
    """Encapsulates outcome of index schema scanning or discovery operations."""
    data_source: str = Field(..., description="Discovered index identifier")
    fields: List[str] = Field(default_factory=list, description="Fields available under the schema")
    is_successful: bool = Field(default=True, description="Indicates if discovery completed without errors")
