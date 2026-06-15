from app.package.manifest import AgentSkillManifest
from app.package.bundle import SkillBundle
from app.domain.models import AgentSkill
from app.agent.graph import ExecutionGraph
from app.agent.governance import GovernancePolicy, ExecutionMode


def test_bundle_construction():
    manifest = AgentSkillManifest(
        skill_name="Test Skill",
        created_at="2026-06-12T00:00:00Z",
        overall_confidence=0.85
    )
    
    graph = ExecutionGraph(nodes=[], edges=[])
    governance = GovernancePolicy(
        approval_required=False,
        execution_mode=ExecutionMode.AUTO
    )
    
    skill = AgentSkill(
        name="Test Skill",
        source_runbook="test_runbook.md",
        graph=graph,
        governance=governance
    )
    
    bundle = SkillBundle(
        manifest=manifest,
        agent_skill=skill,
        diagnostics={"errors": [], "warnings": ["low confidence"]},
        traces=[]
    )
    
    assert bundle.manifest.skill_name == "Test Skill"
    assert bundle.agent_skill.name == "Test Skill"
    assert bundle.diagnostics["warnings"] == ["low confidence"]
    assert len(bundle.traces) == 0
