import os
import tempfile
import pytest
from datetime import datetime, timezone
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.runtime.models import ExecutionStatus
from app.runtime.runner import MockQueryRunner
from app.runtime.engine import ExecutionEngine
from app.agent.graph import ExecutionGraph, ExecutionNode, ExecutionEdge
from app.agent.governance import GovernancePolicy, ExecutionMode
from app.domain.models import AgentSkill, CompiledStep
from app.package.bundle import SkillBundle
from app.package.manifest import AgentSkillManifest


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_engine_workflow_traversal_branching(temp_db):
    repo = SQLiteRepository(temp_db)
    bundle_store = BundleStore(repo)
    engine = ExecutionEngine(
        repo=repo,
        bundle_store=bundle_store,
        audit_repo=None,
        query_runner=MockQueryRunner()
    )

    # 1. Define Execution Nodes
    n1 = ExecutionNode(node_id="node-1", step_id="1", step_name="Check auth", action_type="DETECTION", compiled_spl="index=auth_logs failures")
    n2 = ExecutionNode(node_id="node-2", step_id="2", step_name="Escalate brute force", action_type="ESCALATION", compiled_spl="block ip")
    n3 = ExecutionNode(node_id="node-3", step_id="3", step_name="Log low risk", action_type="LOGGING", compiled_spl="log status")

    # 2. Define Execution Edges with branch conditions
    from app.planner.conditions import BranchCondition
    e1 = ExecutionEdge(
        source="node-1",
        target="node-2",
        condition="failures > 100",
        branch_condition=BranchCondition(expression="failures > 100", operator=">", value="100")
    )
    e2 = ExecutionEdge(
        source="node-1",
        target="node-3",
        condition="failures <= 100",
        branch_condition=BranchCondition(expression="failures <= 100", operator="<=", value="100")
    )

    # 3. Create Execution Graph
    graph = ExecutionGraph(
        nodes=[n1, n2, n3],
        edges=[e1, e2],
        entry_node="node-1"
    )

    # 4. Create Agent Skill Bundle
    gov = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO, audit_enabled=True)
    skill = AgentSkill(name="brute_force_investigation", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="brute_force_investigation", created_at="2026-06-12T00:00:00Z", overall_confidence=1.0)
    bundle = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={"errors": [], "warnings": []}, traces=[])

    # Save bundle in store
    bundle_rec = bundle_store.save_bundle(
        bundle_name="bundle-1",
        skill_bundle=bundle,
        status="COMPLETED",
        created_by="admin",
        tenant_id="tenant-soc"
    )

    # CASE A: Trigger with failures = 120 (Should branch to node-2)
    res_a = engine.execute(
        tenant_id="tenant-soc",
        bundle_id=bundle_rec.bundle_id,
        version=1,
        triggered_by="key-admin",
        initial_input={"failures": 120}
    )
    assert res_a.status == ExecutionStatus.COMPLETED
    assert res_a.current_node_id is None
    # Check node executions
    node_runs_a = repo.get_node_executions("tenant-soc", res_a.execution_id)
    assert len(node_runs_a) == 2
    assert [nr.node_id for nr in node_runs_a] == ["node-1", "node-2"]

    # CASE B: Trigger with failures = 50 (Should branch to node-3)
    res_b = engine.execute(
        tenant_id="tenant-soc",
        bundle_id=bundle_rec.bundle_id,
        version=1,
        triggered_by="key-admin",
        initial_input={"failures": 50}
    )
    assert res_b.status == ExecutionStatus.COMPLETED
    assert res_b.current_node_id is None
    node_runs_b = repo.get_node_executions("tenant-soc", res_b.execution_id)
    assert len(node_runs_b) == 2
    assert [nr.node_id for nr in node_runs_b] == ["node-1", "node-3"]
