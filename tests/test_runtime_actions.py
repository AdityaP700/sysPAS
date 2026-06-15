import pytest
import os
import tempfile
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.runtime.models import ExecutionStatus, FailureCategory
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


def test_runtime_engine_traverses_action_nodes(temp_db):
    repo = SQLiteRepository(temp_db)
    bundle_store = BundleStore(repo)
    
    engine = ExecutionEngine(
        repo=repo,
        bundle_store=bundle_store,
        audit_repo=None,
        query_runner=MockQueryRunner()
    )

    # Node 1: Query (detection)
    n1 = ExecutionNode(node_id="node-1", step_id="1", step_name="Detection Node", action_type="DETECTION", compiled_spl="failures")
    # Node 2: Action (email)
    n2 = ExecutionNode(node_id="node-2", step_id="2", step_name="Email Node", action_type="SEND_EMAIL", compiled_spl="")
    
    e1 = ExecutionEdge(source="node-1", target="node-2")
    graph = ExecutionGraph(nodes=[n1, n2], edges=[e1], entry_node="node-1")
    
    gov = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO, audit_enabled=True)
    skill = AgentSkill(name="action_workflow", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="action_workflow", created_at="2026-06-12T00:00:00Z", overall_confidence=1.0)
    bundle = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={"errors": [], "warnings": []}, traces=[])
    
    bundle_rec = bundle_store.save_bundle(
        bundle_name="bundle-action",
        skill_bundle=bundle,
        status="COMPLETED",
        created_by="admin",
        tenant_id="tenant-soc"
    )

    res = engine.execute(
        tenant_id="tenant-soc",
        bundle_id=bundle_rec.bundle_id,
        version=1,
        triggered_by="key-op",
        initial_input={"failures": 120, "email_to": "ops@runbookmind.local"}
    )
    
    assert res.status == ExecutionStatus.COMPLETED
    assert res.current_node_id is None
    
    # Check node executions were logged
    n_execs = repo.get_node_executions("tenant-soc", res.execution_id)
    assert len(n_execs) == 2
    assert n_execs[0].node_id == "node-1"
    assert n_execs[1].node_id == "node-2"
    assert n_execs[1].status == ExecutionStatus.COMPLETED
    
    # Check that variables were propagated
    assert "node-2" in res.context_payload
    assert res.context_payload["node-2"]["success"] is True
    assert res.context_payload["node-2"]["action_type"] == "SEND_EMAIL"
    
    # Verify action execution record in DB
    act_execs = repo.get_action_executions("tenant-soc", res.execution_id)
    assert len(act_execs) == 1
    assert act_execs[0].action_type == "SEND_EMAIL"
    assert act_execs[0].success is True
