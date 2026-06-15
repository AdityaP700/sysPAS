import os
import tempfile
import pytest
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.runtime.models import ExecutionStatus
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


def test_loop_cycle_protection(temp_db):
    repo = SQLiteRepository(temp_db)
    bundle_store = BundleStore(repo)
    # Set max depth to 4
    engine = ExecutionEngine(
        repo=repo,
        bundle_store=bundle_store,
        audit_repo=None,
        query_runner=MockQueryRunner(),
        max_nodes_executed=4
    )

    n1 = ExecutionNode(node_id="node-1", step_id="1", step_name="Step 1", action_type="DETECTION", compiled_spl="query")
    n2 = ExecutionNode(node_id="node-2", step_id="2", step_name="Step 2", action_type="DETECTION", compiled_spl="query")
    
    # Loop path: n1 -> n2 -> n1
    e1 = ExecutionEdge(source="node-1", target="node-2")
    e2 = ExecutionEdge(source="node-2", target="node-1")

    graph = ExecutionGraph(nodes=[n1, n2], edges=[e1, e2], entry_node="node-1")
    gov = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO, audit_enabled=True)
    skill = AgentSkill(name="loop_workflow", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="loop_workflow", created_at="2026-06-12T00:00:00Z", overall_confidence=1.0)
    bundle = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={"errors": [], "warnings": []}, traces=[])
    bundle_rec = bundle_store.save_bundle(
        bundle_name="bundle-loop",
        skill_bundle=bundle,
        status="COMPLETED",
        created_by="admin",
        tenant_id="tenant-soc"
    )

    # Start loop execution -> should fail when depth reaches 4
    res = engine.execute(
        tenant_id="tenant-soc",
        bundle_id=bundle_rec.bundle_id,
        version=1,
        triggered_by="key-op",
        initial_input={}
    )

    assert res.status == ExecutionStatus.FAILED
    node_runs = repo.get_node_executions("tenant-soc", res.execution_id)
    # Total executed count matches depth limit
    assert len(node_runs) == 4
