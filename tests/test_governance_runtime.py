import pytest
from app.agent.governance import GovernancePolicy, ExecutionMode
from app.runtime.governance import GovernanceRuntime


def test_governance_gate_decisions():
    # 1. AUTO -> CONTINUE
    policy_auto = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO)
    assert GovernanceRuntime.evaluate_gate(policy_auto, "block_ip") == "CONTINUE"

    # 2. HUMAN_IN_LOOP -> PAUSE_APPROVAL
    policy_hil = GovernancePolicy(approval_required=True, execution_mode=ExecutionMode.HUMAN_IN_LOOP)
    assert GovernanceRuntime.evaluate_gate(policy_hil, "block_ip") == "PAUSE_APPROVAL"

    # 3. MANUAL -> STOP_MANUAL
    policy_manual = GovernancePolicy(approval_required=True, execution_mode=ExecutionMode.MANUAL)
    assert GovernanceRuntime.evaluate_gate(policy_manual, "block_ip") == "STOP_MANUAL"
