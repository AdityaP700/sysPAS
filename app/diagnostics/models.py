from pydantic import BaseModel, Field


class CompilationWarning(BaseModel):
    """Represents a non-blocking compiler warning related to a specific step."""
    code: str = Field(..., description="Unique warning code identifier")
    message: str = Field(..., description="Details regarding the warning condition")
    severity: str = Field(default="warning", description="Severity level of the diagnostic event")
    step_id: str = Field(..., description="The step identifier associated with this warning")


class CompilationError(BaseModel):
    """Represents a blocking compiler error that indicates a compilation failure for a step."""
    code: str = Field(..., description="Unique error code identifier")
    message: str = Field(..., description="Details regarding the error condition")
    severity: str = Field(default="error", description="Severity level of the diagnostic event")
    step_id: str = Field(..., description="The step identifier associated with this error")
