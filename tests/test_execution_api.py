import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from app.web.main import app
from app.config.settings import settings
from app.storage.sqlite import SQLiteRepository
from app.auth.api_keys import APIKeyManager
from app.auth.models import GlobalRole, TenantRole
from app.web.dependencies import (
    get_sqlite_repository,
    get_execution_engine,
    get_job_queue,
    get_background_worker,
    get_cron_scheduler,
)
from app.agent.graph import ExecutionGraph, ExecutionNode, ExecutionEdge
from app.agent.governance import GovernancePolicy, ExecutionMode
from app.domain.models import AgentSkill
from app.package.bundle import SkillBundle
from app.package.manifest import AgentSkillManifest
from app.runtime.models import ExecutionStatus, ApprovalStatus


@pytest.fixture
def temp_db_file():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def setup_test_auth(temp_db_file, monkeypatch):
    repo = SQLiteRepository(temp_db_file)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "sqlite_db_path", temp_db_file)

    import app.web.dependencies as deps
    monkeypatch.setattr(deps, "_repo_instance", repo)
    monkeypatch.setattr(deps, "_bundle_store_instance", deps.BundleStore(repo))
    monkeypatch.setattr(deps, "_compilation_store_instance", deps.CompilationStore(repo))
    monkeypatch.setattr(deps, "_trace_store_instance", deps.TraceStore(repo))
    monkeypatch.setattr(deps, "_audit_repo_instance", deps.SQLiteAuditRepository(temp_db_file))
    
    # Engine uses dependencies
    monkeypatch.setattr(deps, "_query_runner_instance", deps.MockQueryRunner())
    monkeypatch.setattr(deps, "_execution_engine_instance", deps.ExecutionEngine(
        repo=repo,
        bundle_store=deps._bundle_store_instance,
        audit_repo=deps._audit_repo_instance,
        query_runner=deps._query_runner_instance,
    ))

    # Queue, Worker, Scheduler
    queue = deps.JobQueue(repo)
    worker = deps.BackgroundWorker(queue, deps._execution_engine_instance)
    scheduler = deps.CronScheduler(repo, queue)

    monkeypatch.setattr(deps, "_job_queue_instance", queue)
    monkeypatch.setattr(deps, "_worker_instance", worker)
    monkeypatch.setattr(deps, "_scheduler_instance", scheduler)

    # Overrides
    app.dependency_overrides[get_sqlite_repository] = lambda: repo
    app.dependency_overrides[get_execution_engine] = lambda: deps._execution_engine_instance
    app.dependency_overrides[get_job_queue] = lambda: queue
    app.dependency_overrides[get_background_worker] = lambda: worker
    app.dependency_overrides[get_cron_scheduler] = lambda: scheduler
    yield repo
    app.dependency_overrides.clear()


def test_execution_api_rbac_and_routing(setup_test_auth):
    repo = setup_test_auth
    manager = APIKeyManager(repo)

    # 1. Create a Global Admin key
    raw_admin_token, admin_key_rec = manager.create_api_key(
        name="Global Admin Key",
        global_role=GlobalRole.ADMIN,
        tenant_id="system"
    )
    admin_headers = {"Authorization": f"Bearer {raw_admin_token}"}

    client = TestClient(app)

    # 2. Register tenant
    create_tenant_resp = client.post(
        "/tenants",
        json={"name": "SOC Team", "slug": "soc"},
        headers=admin_headers
    )
    assert create_tenant_resp.status_code == 200
    tenant_id = create_tenant_resp.json()["tenant_id"]

    # 3. Create Tenant Admin and Tenant Operator keys
    raw_tenant_admin_token, tenant_admin_key = manager.create_api_key(
        name="SOC Admin Key",
        tenant_role=TenantRole.TENANT_ADMIN,
        tenant_id=tenant_id
    )
    tenant_admin_headers = {"Authorization": f"Bearer {raw_tenant_admin_token}"}

    raw_tenant_op_token, tenant_op_key = manager.create_api_key(
        name="SOC Operator Key",
        tenant_role=TenantRole.TENANT_OPERATOR,
        tenant_id=tenant_id
    )
    tenant_op_headers = {"Authorization": f"Bearer {raw_tenant_op_token}"}

    raw_tenant_vw_token, tenant_vw_key = manager.create_api_key(
        name="SOC Viewer Key",
        tenant_role=TenantRole.TENANT_VIEWER,
        tenant_id=tenant_id
    )
    tenant_vw_headers = {"Authorization": f"Bearer {raw_tenant_vw_token}"}

    # Setup memberships
    client.post(f"/tenants/{tenant_id}/memberships", json={"api_key_id": tenant_admin_key.key_id, "role": "TENANT_ADMIN"}, headers=tenant_admin_headers)
    client.post(f"/tenants/{tenant_id}/memberships", json={"api_key_id": tenant_op_key.key_id, "role": "TENANT_OPERATOR"}, headers=tenant_admin_headers)
    client.post(f"/tenants/{tenant_id}/memberships", json={"api_key_id": tenant_vw_key.key_id, "role": "TENANT_VIEWER"}, headers=tenant_admin_headers)

    # 4. Save HIL skill bundle
    n1 = ExecutionNode(node_id="node-1", step_id="1", step_name="Step 1", action_type="DETECTION", compiled_spl="failures")
    graph = ExecutionGraph(nodes=[n1], edges=[], entry_node="node-1")
    gov = GovernancePolicy(approval_required=True, execution_mode=ExecutionMode.HUMAN_IN_LOOP, audit_enabled=True)
    skill = AgentSkill(name="hil_workflow", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="hil_workflow", created_at="2026-06-12T00:00:00Z", overall_confidence=1.0)
    bundle = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={"errors": [], "warnings": []}, traces=[])
    
    # Save bundle under tenant
    deps = get_execution_engine()
    bundle_rec = deps.bundle_store.save_bundle(
        bundle_name="bundle-hil",
        skill_bundle=bundle,
        status="COMPLETED",
        created_by="admin",
        tenant_id=tenant_id
    )
    bundle_id = bundle_rec.bundle_id

    # 5. Trigger run using Tenant Viewer key (Should fail - 403 Forbidden)
    trigger_vw_resp = client.post(
        "/executions/start",
        json={"bundle_id": bundle_id, "input_data": {}},
        headers=tenant_vw_headers
    )
    assert trigger_vw_resp.status_code == 403

    # 6. Trigger run using Tenant Operator key (Should succeed - 200 OK)
    trigger_op_resp = client.post(
        "/executions/start",
        json={"bundle_id": bundle_id, "input_data": {"failures": 120}},
        headers=tenant_op_headers
    )
    assert trigger_op_resp.status_code == 200
    exec_data = trigger_op_resp.json()
    assert exec_data["status"] == "QUEUED"
    exec_id = exec_data["execution_id"]
    job_id = exec_data["job_id"]

    # Run the background worker synchronously to execute the queued job
    queue = get_job_queue()
    worker = get_background_worker()
    job = queue.dequeue(worker.worker_id)
    assert job is not None
    assert job.job_id == job_id
    worker._process_job(job)

    # 7. Get pending approvals using Operator key (Should fail - 403 Forbidden)
    pending_op_resp = client.get(
        "/executions/approvals/pending",
        headers=tenant_op_headers
    )
    assert pending_op_resp.status_code == 403

    # 8. Get pending approvals using Tenant Admin key (Should succeed - 200 OK)
    pending_admin_resp = client.get(
        "/executions/approvals/pending",
        headers=tenant_admin_headers
    )
    assert pending_admin_resp.status_code == 200
    pending_list = pending_admin_resp.json()
    assert len(pending_list) == 1
    assert pending_list[0]["execution_id"] == exec_id
    assert pending_list[0]["node_id"] == "node-1"

    # 9. Resume using Operator key (Should fail - 403 Forbidden)
    resume_op_resp = client.post(
        f"/executions/{exec_id}/resume",
        json={"decision": "APPROVED"},
        headers=tenant_op_headers
    )
    assert resume_op_resp.status_code == 403

    # 10. Resume using Tenant Admin key (Should succeed - 200 OK)
    resume_admin_resp = client.post(
        f"/executions/{exec_id}/resume",
        json={"decision": "APPROVED"},
        headers=tenant_admin_headers
    )
    assert resume_admin_resp.status_code == 200
    
    # We dequeue the resume job and process it synchronously to complete the workflow
    resume_job = queue.dequeue(worker.worker_id)
    assert resume_job is not None
    assert resume_job.payload.get("action") == "resume"
    worker._process_job(resume_job)

    # 11. Read status logs via Viewer key
    status_resp = client.get(f"/executions/{exec_id}", headers=tenant_vw_headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "COMPLETED"

    nodes_resp = client.get(f"/executions/{exec_id}/nodes", headers=tenant_vw_headers)
    assert nodes_resp.status_code == 200
    assert len(nodes_resp.json()) == 1
