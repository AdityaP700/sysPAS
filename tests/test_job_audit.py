import os
import tempfile
import time
from datetime import datetime, timezone
import pytest

from app.storage.sqlite import SQLiteRepository
from app.audit.repository import SQLiteAuditRepository
from app.runtime.runner import MockQueryRunner
from app.runtime.engine import ExecutionEngine
from app.storage.bundle_store import BundleStore
from app.agent.graph import ExecutionGraph, ExecutionNode
from app.agent.governance import GovernancePolicy, ExecutionMode
from app.domain.models import AgentSkill
from app.package.bundle import SkillBundle
from app.package.manifest import AgentSkillManifest
from app.jobs.models import JobRecord, JobStatus
from app.jobs.queue import JobQueue
from app.jobs.worker import BackgroundWorker


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_job_audit_logging(temp_db, monkeypatch):
    repo = SQLiteRepository(temp_db)
    audit_repo = SQLiteAuditRepository(temp_db)
    
    # Monkeypatch dependency provider to return our test audit repo
    import app.web.dependencies as deps
    monkeypatch.setattr(deps, "_audit_repo_instance", audit_repo)

    # 1. Register a bundle
    n1 = ExecutionNode(node_id="node-1", step_id="1", step_name="Step 1", action_type="DETECTION", compiled_spl="query")
    graph = ExecutionGraph(nodes=[n1], edges=[], entry_node="node-1")
    gov = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO, audit_enabled=True)
    skill = AgentSkill(name="test_workflow", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="test_workflow", created_at="2026-06-12T00:00:00Z", overall_confidence=1.0)
    bundle = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={"errors": [], "warnings": []}, traces=[])
    
    bundle_store = BundleStore(repo)
    bundle_rec = bundle_store.save_bundle(
        bundle_name="bundle-test",
        skill_bundle=bundle,
        status="COMPLETED",
        created_by="admin",
        tenant_id="tenant-1"
    )
    
    engine = ExecutionEngine(
        repo=repo,
        bundle_store=bundle_store,
        audit_repo=audit_repo,
        query_runner=MockQueryRunner()
    )
    
    queue = JobQueue(repo)
    
    # Enqueue a job
    execution_id = "exec-audit-1"
    job_id = "job-audit-1"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    job = JobRecord(
        job_id=job_id,
        tenant_id="tenant-1",
        execution_id=execution_id,
        bundle_id=bundle_rec.bundle_id,
        bundle_version=1,
        status=JobStatus.QUEUED,
        created_at=now,
        created_by="key-op",
        payload={"action": "execute", "initial_input": {}},
        priority=100
    )
    
    # Pre-save execution
    engine.repo.save_execution("tenant-1", engine.execute(
        tenant_id="tenant-1",
        bundle_id=bundle_rec.bundle_id,
        version=1,
        triggered_by="key-op",
        initial_input={},
        execution_id=execution_id
    ))

    queue.enqueue(job)
    
    # Process job via worker
    worker = BackgroundWorker(queue=queue, engine=engine)
    dq_job = queue.dequeue(worker.worker_id)
    worker._process_job(dq_job)
    
    # Retrieve audit events
    events = audit_repo.list_audit_events("tenant-1")
    actions = [e.action for e in events]
    
    # Check for JOB_STARTED and JOB_COMPLETED in audit records
    assert "JOB_STARTED" in actions
    assert "JOB_COMPLETED" in actions
