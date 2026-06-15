from typing import List
from pydantic import BaseModel, Field
from app.package.bundle import SkillBundle


class CompileRunbookResponse(BaseModel):
    """API response contract representing successful or partial runbook compilation outcomes."""
    status: str = Field(
        ...,
        description="The outcome status of the compilation pipeline (e.g. 'SUCCESS', 'PARTIAL', 'FAILED')"
    )
    runbook_name: str = Field(
        ...,
        description="The parsed name of the runbook compiled"
    )
    bundle: SkillBundle = Field(
        ...,
        description="The packaged skill bundle payload"
    )
    errors: List[str] = Field(
        default_factory=list,
        description="Validation or compiler errors surfaced cleanly"
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Validation or compiler warnings surfaced cleanly"
    )


class SkillBundleResponse(BaseModel):
    """API contract representing retrieved or exported skill configuration bundles."""
    bundle_id: str = Field(
        ...,
        description="Unique identifier hash of the bundle package"
    )
    bundle: SkillBundle = Field(
        ...,
        description="The packaged skill bundle payload"
    )
    exported_at: str = Field(
        ...,
        description="ISO 8601 timestamp details of when this response was generated"
    )
