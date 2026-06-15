import pytest
from typing import Any
from app.governance.models import PolicyRecord, PolicyType, PolicyDecision
from app.governance.policy_engine import PolicyEngine


class MockRepo:
    def __init__(self, policies=None, system_flags=None):
        self.policies = policies or []
        self.system_flags = system_flags or {}
        self.events = []

    def list_policies(self, tenant_id: str):
        return [p for p in self.policies if p.tenant_id == tenant_id]

    def get_system_flag(self, flag_name: str):
        return self.system_flags.get(flag_name)

    def save_policy_event(self, tenant_id: str, event: Any):
        self.events.append(event)


def test_rule_matching_and_conflict_resolution():
    # 1. Higher priority wins (ALLOW wins over DENY because of higher priority)
    p_allow = PolicyRecord(
        policy_id="p1",
        tenant_id="tenant1",
        name="Allow Jira",
        policy_type=PolicyType.EXECUTION,
        enabled=True,
        priority=120,
        version=1,
        is_current=True,
        policy_definition=[
            {
                "if": {"connector_type": "JIRA"},
                "then": {"allowed": True, "message": "Allow Jira always"}
            }
        ],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )

    p_deny = PolicyRecord(
        policy_id="p2",
        tenant_id="tenant1",
        name="Deny Jira",
        policy_type=PolicyType.EXECUTION,
        enabled=True,
        priority=100,
        version=1,
        is_current=True,
        policy_definition=[
            {
                "if": {"connector_type": "JIRA"},
                "then": {"allowed": False, "message": "Jira is blocked"}
            }
        ],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )

    repo = MockRepo(policies=[p_allow, p_deny])
    engine = PolicyEngine(repo)

    decision = engine.evaluate("tenant1", PolicyType.EXECUTION, {"connector_type": "JIRA"})
    assert decision.allowed is True
    assert decision.matched_policy_id == "p1"


def test_conflict_resolution_deny_overrides_allow_at_equal_priority():
    # 2. Deny wins when priorities are equal (both priority 100)
    p_allow = PolicyRecord(
        policy_id="p1",
        tenant_id="tenant1",
        name="Allow Jira",
        policy_type=PolicyType.EXECUTION,
        enabled=True,
        priority=100,
        version=1,
        is_current=True,
        policy_definition=[
            {
                "if": {"connector_type": "JIRA"},
                "then": {"allowed": True, "message": "Allow Jira always"}
            }
        ],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )

    p_deny = PolicyRecord(
        policy_id="p2",
        tenant_id="tenant1",
        name="Deny Jira",
        policy_type=PolicyType.EXECUTION,
        enabled=True,
        priority=100,
        version=1,
        is_current=True,
        policy_definition=[
            {
                "if": {"connector_type": "JIRA"},
                "then": {"allowed": False, "message": "Jira is blocked"}
            }
        ],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )

    repo = MockRepo(policies=[p_allow, p_deny])
    engine = PolicyEngine(repo)

    decision = engine.evaluate("tenant1", PolicyType.EXECUTION, {"connector_type": "JIRA"})
    assert decision.allowed is False
    assert decision.matched_policy_id == "p2"
    assert "Jira is blocked" in decision.violations


def test_global_system_flag_kill_switch():
    repo = MockRepo(system_flags={"WORKFLOW_EXECUTION_DISABLED": "true"})
    engine = PolicyEngine(repo)

    decision = engine.evaluate("tenant1", PolicyType.EXECUTION, {"connector_type": "JIRA"})
    assert decision.allowed is False
    assert "Global kill switch active" in decision.violations[0]


def test_policy_simulation_mode():
    engine = PolicyEngine(MockRepo())
    definition = [
        {
            "if": {"connector_type": "PAGERDUTY"},
            "then": {"allowed": False, "message": "PagerDuty is not allowed in simulation"}
        }
    ]

    decision = engine.simulate("tenant1", {"connector_type": "PAGERDUTY"}, definition)
    assert decision.allowed is False
    assert decision.matched_policy_id == "simulation"
    assert "PagerDuty is not allowed in simulation" in decision.violations
