from typing import Dict, List, Any
from pydantic import BaseModel, Field
from app.package.manifest import AgentSkillManifest
from app.domain.models import AgentSkill
from app.tracing.models import CompilationTrace


class SkillBundle(BaseModel):
    """Integrates manifest metadata, AgentSkill flow, step traces, and compile-time diagnostics."""
    manifest: AgentSkillManifest = Field(
        ...,
        description="Release metadata manifest information"
    )
    agent_skill: AgentSkill = Field(
        ...,
        description="The compiled AgentSkill workflow graph and policy details"
    )
    diagnostics: Dict[str, List[Any]] = Field(
        default_factory=dict,
        description="Compilation diagnostics warnings and errors mapped by type"
    )
    traces: List[CompilationTrace] = Field(
        default_factory=list,
        description="Query optimization and execution trace steps"
    )
