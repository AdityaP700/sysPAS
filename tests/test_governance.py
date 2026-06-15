from app.domain.enums import StepType
from app.domain.models import Runbook, RunbookStep
from app.agent.compiler import AgentSkillCompiler
from app.agent.governance import ExecutionMode


def test_governance_inference_auto():
    step = RunbookStep(
        step_id="1",
        description="Create JIRA ticket for security alert",
        step_type=StepType.ACTION,
        action="create JIRA ticket"
    )
    runbook = Runbook(name="Jira SOP", steps=[step])
    compiler = AgentSkillCompiler()
    
    policy = compiler._infer_governance_policy(runbook)
    assert policy.execution_mode == ExecutionMode.AUTO
    assert policy.approval_required is False
    assert policy.approval_role is None
    assert policy.audit_enabled is True


def test_governance_inference_human_in_loop():
    step = RunbookStep(
        step_id="1",
        description="Block offending IP address",
        step_type=StepType.ACTION,
        action="block IP address"
    )
    runbook = Runbook(name="Block SOP", steps=[step])
    compiler = AgentSkillCompiler()
    
    policy = compiler._infer_governance_policy(runbook)
    assert policy.execution_mode == ExecutionMode.HUMAN_IN_LOOP
    assert policy.approval_required is True
    assert policy.approval_role == "soc_analyst"


def test_governance_inference_manual():
    step = RunbookStep(
        step_id="1",
        description="Perform manual verification",
        step_type=StepType.MANUAL,
        action="verify logs manually"
    )
    runbook = Runbook(name="Manual SOP", steps=[step])
    compiler = AgentSkillCompiler()
    
    policy = compiler._infer_governance_policy(runbook)
    assert policy.execution_mode == ExecutionMode.MANUAL
    assert policy.approval_required is True
    assert policy.approval_role == "operator"
