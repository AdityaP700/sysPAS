import os
import tempfile
import pytest
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.runtime.models import ExecutionStatus, ApprovalStatus
from app.runtime.runner import MockQueryRunner
from app.runtime.engine import ExecutionEngine
from app.agent.graph import ExecutionGraph, ExecutionNode, ExecutionEdge
from app.agent.governance import GovernancePolicy, ExecutionMode
from app.domain.models import AgentSkill
from app.package.bundle import SkillBundle
from app.package.manifest import AgentSkillManifest


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_workflow_pause_and_resume_flow(temp_db):
    repo = SQLiteRepository(temp_db)
    bundle_store = BundleStore(repo)
    engine = ExecutionEngine(
        repo=repo,
        bundle_store=bundle_store,
        audit_repo=None,
        query_runner=MockQueryRunner()
    )

    n1 = ExecutionNode(node_id="node-1", step_id="1", step_name="Step 1", action_type="DETECTION", compiled_spl="failures")
    graph = ExecutionGraph(nodes=[n1], edges=[], entry_node="node-1")
    
    # Mode = HUMAN_IN_LOOP
    gov = GovernancePolicy(approval_required=True, execution_mode=ExecutionMode.HUMAN_IN_LOOP, audit_enabled=True)
    skill = AgentSkill(name="hil_workflow", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="hil_workflow", created_at="2026-06-12T00:00:00Z", overall_confidence=1.0)
    bundle = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={"errors": [], "warnings": []}, traces=[])
    bundle_rec = bundle_store.save_bundle(
        bundle_name="bundle-hil",
        skill_bundle=bundle,
        status="COMPLETED",
        created_by="admin",
        tenant_id="tenant-soc"
    )

    # 1. Trigger execution -> Should pause at node-1 for approval
    res = engine.execute(
        tenant_id="tenant-soc",
        bundle_id=bundle_rec.bundle_id,
        version=1,
        triggered_by="key-op",
        initial_input={}
    )
    
    assert res.status == ExecutionStatus.RUNNING
    assert res.current_node_id == "node-1"
    
    # Check pending approval record
    pending = repo.list_pending_approvals("tenant-soc")
    assert len(pending) == 1
    assert pending[0].node_id == "node-1"
    assert pending[0].decision is None

    # 2. Resume with REJECTED
    res_rejected = engine.resume(
        execution_id=res.execution_id,
        decider_id="key-admin",
        decision=ApprovalStatus.REJECTED,
        tenant_id="tenant-soc"
    )
    assert res_rejected.status == ExecutionStatus.FAILED
    assert repo.get_execution("tenant-soc", res.execution_id).status == ExecutionStatus.FAILED

    # Check approval record decision
    appr = repo.get_approval_by_execution("tenant-soc", res.execution_id)
    assert appr.decision == ApprovalStatus.REJECTED
    assert appr.decided_by == "key-admin"

    # CASE B: Trigger new run, approve it
    res2 = engine.execute(
        tenant_id="tenant-soc",
        bundle_id=bundle_rec.bundle_id,
        version=1,
        triggered_by="key-op",
        initial_input={}
    )
    assert res2.status == ExecutionStatus.RUNNING
    assert res2.current_node_id == "node-1"

    # Resume with APPROVED
    res_approved = engine.resume(
        execution_id=res2.execution_id,
        decider_id="key-admin",
        decision=ApprovalStatus.APPROVED,
        tenant_id="tenant-soc"
    )
    assert res_approved.status == ExecutionStatus.COMPLETED
    assert res_approved.current_node_id is None
    
    appr2 = repo.get_approval_by_execution("tenant-soc", res2.execution_id)
    assert appr2.decision == ApprovalStatus.APPROVED
    assert appr2.decided_by == "key-admin"
