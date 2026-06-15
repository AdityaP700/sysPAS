from pydantic import BaseModel, Field


class AgentSkillManifest(BaseModel):
    """Holds metadata summary details of compiled agent skills."""
    skill_name: str = Field(
        ...,
        description="Name of the compiled Agent Skill"
    )
    version: str = Field(
        default="1.0.0",
        description="The release version of the skill configuration"
    )
    compiler_version: str = Field(
        default="1.0.0",
        description="The compiler version utilized during build compilation"
    )
    created_at: str = Field(
        ...,
        description="Time of creation in ISO 8601 format"
    )
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Aggregated compilation confidence score"
    )
