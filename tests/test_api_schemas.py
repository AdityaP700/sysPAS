from app.api.schemas import CompileRunbookResponse, SkillBundleResponse
from app.package.manifest import AgentSkillManifest
from app.package.bundle import SkillBundle
from app.domain.models import AgentSkill
from app.agent.graph import ExecutionGraph
from app.agent.governance import GovernancePolicy, ExecutionMode


def test_api_response_serialization():
    manifest = AgentSkillManifest(
        skill_name="Test Skill",
        created_at="2026-06-12T00:00:00Z",
        overall_confidence=0.9
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
        diagnostics={"errors": [], "warnings": []},
        traces=[]
    )
    
    # 1. CompileRunbookResponse
    response1 = CompileRunbookResponse(
        status="SUCCESS",
        runbook_name="Test Runbook",
        bundle=bundle,
        errors=[],
        warnings=[]
    )
    
    serialized1 = response1.model_dump_json()
    loaded1 = CompileRunbookResponse.model_validate_json(serialized1)
    assert loaded1.status == "SUCCESS"
    assert loaded1.runbook_name == "Test Runbook"
    assert loaded1.bundle.manifest.skill_name == "Test Skill"
    
    # 2. SkillBundleResponse
    response2 = SkillBundleResponse(
        bundle_id="bundle_abc_123",
        bundle=bundle,
        exported_at="2026-06-12T00:01:00Z"
    )
    
    serialized2 = response2.model_dump_json()
    loaded2 = SkillBundleResponse.model_validate_json(serialized2)
    assert loaded2.bundle_id == "bundle_abc_123"
    assert loaded2.exported_at == "2026-06-12T00:01:00Z"
    assert loaded2.bundle.agent_skill.source_runbook == "test_runbook.md"
