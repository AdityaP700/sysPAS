import json
from app.domain.enums import StepType, ActionType, CompilationStatus
from app.domain.models import Runbook, RunbookStep, CompiledStep, CompilationResult, AgentSkill


def test_runbook_step_serialization():
    step = RunbookStep(
        step_id="1",
        description="Check auth logs for spikes",
        step_type=StepType.DETECTION,
        data_source="auth_logs",
        condition="failures > 100",
        time_window="5m",
        confidence=0.95
    )
    
    # Serialize to dict and json
    serialized_dict = step.model_dump()
    serialized_json = step.model_dump_json()
    
    assert serialized_dict["step_id"] == "1"
    assert serialized_dict["step_type"] == StepType.DETECTION
    assert serialized_dict["confidence"] == 0.95
    
    # Deserialize back
    parsed_step = RunbookStep.model_validate_json(serialized_json)
    assert parsed_step.step_id == "1"
    assert parsed_step.step_type == StepType.DETECTION
    assert parsed_step.confidence == 0.95


def test_runbook_serialization():
    step1 = RunbookStep(
        step_id="1",
        description="Check auth logs for spikes",
        step_type=StepType.DETECTION,
        data_source="auth_logs",
        condition="failures > 100",
        time_window="5m"
    )
    step2 = RunbookStep(
        step_id="2",
        description="Block source IP",
        step_type=StepType.ACTION,
        action="block IP address"
    )
    
    runbook = Runbook(
        name="Failed Login Investigation",
        description="Investigates spikes in failed authentication logins.",
        steps=[step1, step2],
        metadata={"version": "1.0.0"}
    )
    
    serialized_json = runbook.model_dump_json()
    parsed_runbook = Runbook.model_validate_json(serialized_json)
    
    assert parsed_runbook.name == "Failed Login Investigation"
    assert parsed_runbook.description == "Investigates spikes in failed authentication logins."
    assert len(parsed_runbook.steps) == 2
    assert parsed_runbook.steps[0].step_id == "1"
    assert parsed_runbook.steps[1].step_type == StepType.ACTION
    assert parsed_runbook.metadata["version"] == "1.0.0"


def test_compilation_result_serialization():
    compiled_step = CompiledStep(
        step_id="1",
        description="Check auth logs for spikes",
        raw_spl="index=auth | stats count",
        compiled_spl="| tstats count WHERE index=auth",
        explanation="Uses tstats for faster indexing.",
        status=CompilationStatus.SUCCESS,
        confidence=0.9
    )
    
    result = CompilationResult(
        runbook_name="Failed Login Investigation",
        steps=[compiled_step],
        status=CompilationStatus.SUCCESS
    )
    
    serialized_json = result.model_dump_json()
    parsed_result = CompilationResult.model_validate_json(serialized_json)
    
    assert parsed_result.runbook_name == "Failed Login Investigation"
    assert len(parsed_result.steps) == 1
    assert parsed_result.steps[0].compiled_spl == "| tstats count WHERE index=auth"
    assert parsed_result.status == CompilationStatus.SUCCESS


def test_agent_skill_serialization():
    from app.agent.graph import ExecutionGraph, ExecutionNode
    from app.agent.governance import GovernancePolicy, ExecutionMode

    compiled_step = CompiledStep(
        step_id="1",
        description="Check auth logs for spikes",
        compiled_spl="| tstats count WHERE index=auth",
        status=CompilationStatus.SUCCESS
    )
    
    node = ExecutionNode(
        node_id="node_1",
        step_id="1",
        step_name="Check auth logs for spikes",
        action_type="DETECTION",
        compiled_spl="| tstats count WHERE index=auth",
        confidence=1.0
    )
    graph = ExecutionGraph(
        nodes=[node],
        edges=[],
        entry_node="node_1"
    )
    governance = GovernancePolicy(
        approval_required=True,
        approval_role="soc_analyst",
        audit_enabled=True,
        execution_mode=ExecutionMode.HUMAN_IN_LOOP
    )

    skill = AgentSkill(
        name="Failed Login Skill",
        source_runbook="failed_login_sop.md",
        steps=[compiled_step],
        graph=graph,
        governance=governance
    )
    
    serialized_json = skill.model_dump_json()
    parsed_skill = AgentSkill.model_validate_json(serialized_json)
    
    assert parsed_skill.name == "Failed Login Skill"
    assert parsed_skill.source_runbook == "failed_login_sop.md"
    assert len(parsed_skill.steps) == 1
    assert parsed_skill.governance.execution_mode == ExecutionMode.HUMAN_IN_LOOP
    assert parsed_skill.governance.approval_role == "soc_analyst"
