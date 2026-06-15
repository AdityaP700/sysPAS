import os
import tempfile
import time
from datetime import datetime, timezone
import pytest

from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.runtime.models import ExecutionStatus
from app.runtime.runner import MockQueryRunner
from app.runtime.engine import ExecutionEngine
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


def test_worker_processing_flow(temp_db):
    repo = SQLiteRepository(temp_db)
    bundle_store = BundleStore(repo)
    
    # 1. Register a mock execution bundle
    n1 = ExecutionNode(node_id="node-1", step_id="1", step_name="Step 1", action_type="DETECTION", compiled_spl="query")
    graph = ExecutionGraph(nodes=[n1], edges=[], entry_node="node-1")
    gov = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO, audit_enabled=True)
    skill = AgentSkill(name="test_workflow", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="test_workflow", created_at="2026-06-12T00:00:00Z", overall_confidence=1.0)
    bundle = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={"errors": [], "warnings": []}, traces=[])
    
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
        audit_repo=None,
        query_runner=MockQueryRunner()
    )
    
    queue = JobQueue(repo)
    
    # 2. Enqueue a job
    execution_id = "exec-test-1"
    job_id = "job-test-1"
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
    
    # Create the PENDING execution record first (matching POST /start route logic)
    engine.repo.save_execution("tenant-1", engine.execute(
        tenant_id="tenant-1",
        bundle_id=bundle_rec.bundle_id,
        version=1,
        triggered_by="key-op",
        initial_input={},
        execution_id=execution_id
    ))
    
    queue.enqueue(job)
    
    # 3. Instantiate worker and process job directly (avoid spawning thread in unit test to keep it synchronous)
    worker = BackgroundWorker(queue=queue, engine=engine)
    dq_job = queue.dequeue(worker.worker_id)
    assert dq_job is not None
    
    worker._process_job(dq_job)
    
    # Check that job status transitioned to COMPLETED
    completed_job = queue.get_job("tenant-1", job_id)
    assert completed_job.status == JobStatus.COMPLETED
    
    # Check that execution status transitioned to COMPLETED
    exec_rec = repo.get_execution("tenant-1", execution_id)
    assert exec_rec.status == ExecutionStatus.COMPLETED
