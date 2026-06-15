import os
import tempfile
import pytest
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.audit.repository import SQLiteAuditRepository
from app.runtime.models import ExecutionStatus, ApprovalStatus
from app.runtime.runner import MockQueryRunner
from app.runtime.engine import ExecutionEngine
from app.agent.graph import ExecutionGraph, ExecutionNode
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


def test_runtime_audit_logs(temp_db):
    repo = SQLiteRepository(temp_db)
    bundle_store = BundleStore(repo)
    audit_repo = SQLiteAuditRepository(temp_db)
    
    engine = ExecutionEngine(
        repo=repo,
        bundle_store=bundle_store,
        audit_repo=audit_repo,
        query_runner=MockQueryRunner()
    )

    n1 = ExecutionNode(node_id="node-1", step_id="1", step_name="Step 1", action_type="DETECTION", compiled_spl="failures")
    graph = ExecutionGraph(nodes=[n1], edges=[], entry_node="node-1")
    
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

    # Start Execution -> generates START_EXECUTION and APPROVAL_REQUESTED
    res = engine.execute(
        tenant_id="tenant-soc",
        bundle_id=bundle_rec.bundle_id,
        version=1,
        triggered_by="key-op",
        initial_input={}
    )
    
    events = audit_repo.list_audit_events("tenant-soc")
    actions = [e.action for e in events]
    assert "START_EXECUTION" in actions
    assert "APPROVAL_REQUESTED" in actions

    # Resume with approval -> generates APPROVAL_GRANTED, NODE_EXECUTED, and EXECUTION_COMPLETED
    engine.resume(
        execution_id=res.execution_id,
        decider_id="key-admin",
        decision=ApprovalStatus.APPROVED,
        tenant_id="tenant-soc"
    )
    
    events2 = audit_repo.list_audit_events("tenant-soc")
    actions2 = [e.action for e in events2]
    assert "APPROVAL_GRANTED" in actions2
    assert "NODE_EXECUTED" in actions2
    assert "EXECUTION_COMPLETED" in actions2
