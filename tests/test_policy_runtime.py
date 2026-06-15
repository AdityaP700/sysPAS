import pytest
import os
import time
import tempfile
from datetime import datetime, timezone
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.storage.models import BundleRecord
from app.vault.service import VaultService
from app.vault.models import SecretRecord, SecretType
from app.runtime.engine import ExecutionEngine
from app.runtime.models import ExecutionStatus, FailureCategory
from app.governance.models import PolicyRecord, PolicyType
from app.domain.models import Runbook, RunbookStep, StepType


class MockQueryRunner:
    def run_query(self, query: str, context: dict):
        return {"status": "ok"}


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = SQLiteRepository(path)
    
    yield repo
    
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_vault_secret_policy_gating_and_ttl(temp_db):
    repo = temp_db
    vault = VaultService(repo)
    
    # 1. Create a DEV secret
    secret = vault.create_secret("tenant-1", "my-api-key", SecretType.API_KEY, "plaintext123", environment="DEV")
    assert secret.version == 1
    assert secret.environment == "DEV"

    # 2. Add an execution policy allowing all secrets in DEV
    # But let's check without policies first - should allow by default
    val = vault.resolve_secret("tenant-1", "my-api-key")
    assert val == "plaintext123"

    # 3. Add a policy denying access to DEV secrets in PRODUCTION executions
    p_deny = PolicyRecord(
        policy_id="p-sec-1",
        tenant_id="tenant-1",
        name="No DEV secrets in prod",
        policy_type=PolicyType.SECRET,
        enabled=True,
        priority=100,
        version=1,
        is_current=True,
        policy_definition=[
            {
                "if": {"secret_environment": "DEV", "environment": "PRODUCTION"},
                "then": {"allowed": False, "message": "DEV secret blocked in PRODUCTION"}
            }
        ],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )
    repo.save_policy("tenant-1", p_deny)

    # 4. Mock a running execution in PRODUCTION environment
    # Let's save a bundle with environment PRODUCTION
    bundle = BundleRecord(
        bundle_id="b-prod-1",
        bundle_name="Prod SOP",
        version=1,
        created_at="2026-06-13T12:00:00Z",
        status="COMPILED",
        payload={"steps": []},
        tenant_id="tenant-1",
        created_by="system",
        environment="PRODUCTION",
        promotion_status="APPROVED"
    )
    repo.save_bundle("tenant-1", bundle)

    # Create active running execution referencing this bundle
    from app.runtime.models import ExecutionRecord, ExecutionStatus
    exec_rec = ExecutionRecord(
        execution_id="exec-prod-1",
        tenant_id="tenant-1",
        bundle_id="b-prod-1",
        bundle_version=1,
        status=ExecutionStatus.RUNNING,
        current_node_id="1",
        started_at="2026-06-13T12:00:00Z",
        triggered_by="admin",
        context_payload={}
    )
    repo.save_execution("tenant-1", exec_rec)

    # 5. Resolve secret - should fail because active execution environment is PRODUCTION and secret is DEV
    vault._decision_cache.clear()
    with pytest.raises(ValueError) as exc:
        vault.resolve_secret("tenant-1", "my-api-key")
    assert "DEV secret blocked in PRODUCTION" in str(exc.value)

    # 6. Disable the execution by marking it completed (meaning no active prod execution)
    exec_rec.status = ExecutionStatus.COMPLETED
    repo.save_execution("tenant-1", exec_rec)

    # Clean the decision cache in VaultService so it checks DB/Engine again
    vault._decision_cache.clear()

    # Now resolving should succeed (falls back to DEV context environment)
    assert vault.resolve_secret("tenant-1", "my-api-key") == "plaintext123"


def test_runtime_step_by_step_re_evaluation(temp_db):
    repo = temp_db
    bundle_store = BundleStore(repo)
    query_runner = MockQueryRunner()
    engine = ExecutionEngine(
        repo=repo,
        bundle_store=bundle_store,
        audit_repo=None,
        query_runner=query_runner
    )

    # 1. Save a bundle with 2 action steps
    from app.agent.graph import ExecutionGraph, ExecutionNode, ExecutionEdge
    from app.agent.governance import GovernancePolicy, ExecutionMode
    from app.domain.models import AgentSkill
    from app.package.bundle import SkillBundle
    from app.package.manifest import AgentSkillManifest

    n1 = ExecutionNode(node_id="step-1", step_id="1", step_name="JIRA step", action_type="JIRA")
    n2 = ExecutionNode(node_id="step-2", step_id="2", step_name="PD step", action_type="PAGERDUTY")
    e1 = ExecutionEdge(source="step-1", target="step-2")
    graph = ExecutionGraph(nodes=[n1, n2], edges=[e1], entry_node="step-1")
    gov = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO)
    skill = AgentSkill(name="Multi-Step runbook", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="Multi-Step runbook", created_at="2026-06-13T12:00:00Z", overall_confidence=1.0)
    bundle_model = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={}, traces=[])
    payload = bundle_model.model_dump()
    
    bundle = BundleRecord(
        bundle_id="b-run-1",
        bundle_name="Multi SOP",
        version=1,
        created_at="2026-06-13T12:00:00Z",
        status="COMPILED",
        payload=payload,
        tenant_id="tenant-1",
        created_by="system",
        environment="DEV",
        promotion_status="DRAFT"
    )
    repo.save_bundle("tenant-1", bundle)

    # 2. Define a policy that allows JIRA but blocks PAGERDUTY
    p_deny_pd = PolicyRecord(
        policy_id="p-pd-1",
        tenant_id="tenant-1",
        name="Block PagerDuty",
        policy_type=PolicyType.EXECUTION,
        enabled=True,
        priority=100,
        version=1,
        is_current=True,
        policy_definition=[
            {
                "if": {"connector_type": "PAGERDUTY"},
                "then": {"allowed": False, "message": "PagerDuty is denylisted"}
            }
        ],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )
    repo.save_policy("tenant-1", p_deny_pd)

    # 3. Trigger execution. Step 1 (JIRA) should evaluate successfully and write node execution.
    # But step 2 (PAGERDUTY) should fail, causing the overall execution to fail with POLICY_VIOLATION.
    exec_rec = engine.execute(
        tenant_id="tenant-1",
        bundle_id="b-run-1",
        version=1,
        triggered_by="admin-key",
        initial_input={}
    )

    # Verify execution overall status
    assert exec_rec.status == ExecutionStatus.FAILED
    assert exec_rec.failure_category == FailureCategory.POLICY_VIOLATION
    assert exec_rec.current_node_id == "step-2"

    # Verify step-1 succeeded in node execution list but step-2 was not completed
    nodes = repo.get_node_executions("tenant-1", exec_rec.execution_id)
    assert len(nodes) == 1
    assert nodes[0].node_id == "step-1"
    assert nodes[0].status == ExecutionStatus.COMPLETED
