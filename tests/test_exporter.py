import json
from app.package.manifest import AgentSkillManifest
from app.package.bundle import SkillBundle
from app.package.exporter import SkillExporter
from app.domain.models import AgentSkill
from app.agent.graph import ExecutionGraph
from app.agent.governance import GovernancePolicy, ExecutionMode


def test_exporter_deterministic_json():
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
    
    # Export
    json_str = SkillExporter.export_json(bundle)
    
    # Assert JSON sorting
    loaded = json.loads(json_str)
    assert loaded["manifest"]["skill_name"] == "Test Skill"
    
    # Verify standard indentation and sort keys
    manual_sorted_json = json.dumps(bundle.model_dump(), sort_keys=True, indent=2)
    assert json_str == manual_sorted_json

    # Test dictionary export
    dict_data = SkillExporter.export_dict(bundle)
    assert dict_data["manifest"]["skill_name"] == "Test Skill"
    assert dict_data["agent_skill"]["source_runbook"] == "test_runbook.md"
