import json
from app.domain.enums import StepType, CompilationStatus
from app.domain.models import Runbook, RunbookStep, CompilationResult, CompiledStep
from app.agent.compiler import AgentSkillCompiler
from app.agent.governance import ExecutionMode


def test_agent_skill_compilation():
    # Setup source Runbook
    step1 = RunbookStep(
        step_id="1",
        description="Check auth logs for authentication errors",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    step2 = RunbookStep(
        step_id="2",
        description="If external, block offending IP",
        step_type=StepType.ACTION,
        condition="external",
        action="block IP address",
        confidence=0.95
    )
    runbook = Runbook(name="Failed Logins Runbook", steps=[step1, step2])

    # Setup CompilationResult
    c_step1 = CompiledStep(
        step_id="1",
        description="Check auth logs for authentication errors",
        raw_spl="index=auth",
        compiled_spl="index=auth earliest=-15m",
        explanation="Search index auth",
        status=CompilationStatus.SUCCESS,
        confidence=0.9
    )
    c_step2 = CompiledStep(
        step_id="2",
        description="If external, block offending IP",
        raw_spl="index=auth | block",
        compiled_spl="index=auth | block earliest=-15m",
        explanation="Block IP query",
        status=CompilationStatus.SUCCESS,
        confidence=0.95
    )
    comp_result = CompilationResult(
        runbook_name="Failed Logins Runbook",
        steps=[c_step1, c_step2],
        status=CompilationStatus.SUCCESS
    )

    # Compile to AgentSkill
    compiler = AgentSkillCompiler()
    skill = compiler.compile_skill(runbook, comp_result)

    # Assert graph nodes
    assert skill.name == "Failed Logins Runbook Skill"
    assert skill.source_runbook == "failed_logins_runbook_sop.md"
    assert skill.compiler_version == "1.0.0"
    
    assert len(skill.graph.nodes) == 2
    assert skill.graph.nodes[0].node_id == "node_1"
    assert skill.graph.nodes[0].step_id == "1"
    assert skill.graph.nodes[0].compiled_spl == "index=auth earliest=-15m"
    assert skill.graph.nodes[0].confidence == 0.9

    assert skill.graph.nodes[1].node_id == "node_2"
    assert skill.graph.nodes[1].step_id == "2"
    assert skill.graph.nodes[1].action_type == "BLOCK_IP"

    # Assert edges
    assert len(skill.graph.edges) == 1
    assert skill.graph.edges[0].source == "node_1"
    assert skill.graph.edges[0].target == "node_2"
    assert skill.graph.edges[0].condition == "external"

    # Assert governance
    assert skill.governance.execution_mode == ExecutionMode.HUMAN_IN_LOOP
    assert skill.governance.approval_required is True
    assert skill.governance.approval_role == "soc_analyst"

    # Export Support (model_dump_json)
    skill_json_str = skill.model_dump_json()
    assert isinstance(skill_json_str, str)
    
    # Reload and assert fields are valid JSON
    loaded_dict = json.loads(skill_json_str)
    assert loaded_dict["name"] == "Failed Logins Runbook Skill"
    assert loaded_dict["governance"]["execution_mode"] == "HUMAN_IN_LOOP"
    assert loaded_dict["graph"]["entry_node"] == "node_1"
    assert len(loaded_dict["graph"]["nodes"]) == 2
