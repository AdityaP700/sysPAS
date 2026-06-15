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
    
    monkeypatch.setattr(deps, "_query_runner_instance", deps.MockQueryRunner())
    monkeypatch.setattr(deps, "_execution_engine_instance", deps.ExecutionEngine(
        repo=repo,
        bundle_store=deps._bundle_store_instance,
        audit_repo=deps._audit_repo_instance,
        query_runner=deps._query_runner_instance,
    ))

    queue = deps.JobQueue(repo)
    worker = deps.BackgroundWorker(queue, deps._execution_engine_instance)
    scheduler = deps.CronScheduler(repo, queue)

    monkeypatch.setattr(deps, "_job_queue_instance", queue)
    monkeypatch.setattr(deps, "_worker_instance", worker)
    monkeypatch.setattr(deps, "_scheduler_instance", scheduler)

    app.dependency_overrides[get_sqlite_repository] = lambda: repo
    app.dependency_overrides[get_execution_engine] = lambda: deps._execution_engine_instance
    app.dependency_overrides[get_job_queue] = lambda: queue
    app.dependency_overrides[get_background_worker] = lambda: worker
    app.dependency_overrides[get_cron_scheduler] = lambda: scheduler
    yield repo
    app.dependency_overrides.clear()


def test_execution_results_api_success_and_isolation(setup_test_auth):
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

    # 2. Register Tenant A and Tenant B
    tenant_a_resp = client.post("/tenants", json={"name": "Tenant A", "slug": "t-a"}, headers=admin_headers)
    tenant_b_resp = client.post("/tenants", json={"name": "Tenant B", "slug": "t-b"}, headers=admin_headers)
    
    tenant_a_id = tenant_a_resp.json()["tenant_id"]
    tenant_b_id = tenant_b_resp.json()["tenant_id"]

    # 3. Create Tenant Admin/Viewer keys for both tenants
    raw_a_admin, key_a_admin = manager.create_api_key(name="A Admin", tenant_role=TenantRole.TENANT_ADMIN, tenant_id=tenant_a_id)
    raw_a_viewer, key_a_viewer = manager.create_api_key(name="A Viewer", tenant_role=TenantRole.TENANT_VIEWER, tenant_id=tenant_a_id)
    raw_b_viewer, key_b_viewer = manager.create_api_key(name="B Viewer", tenant_role=TenantRole.TENANT_VIEWER, tenant_id=tenant_a_id) # wait, key belongs to tenant B
    
    # recreate key_b_viewer properly scoped to tenant B
    raw_b_viewer, key_b_viewer = manager.create_api_key(name="B Viewer", tenant_role=TenantRole.TENANT_VIEWER, tenant_id=tenant_b_id)

    client.post(f"/tenants/{tenant_a_id}/memberships", json={"api_key_id": key_a_admin.key_id, "role": "TENANT_ADMIN"}, headers=admin_headers)
    client.post(f"/tenants/{tenant_a_id}/memberships", json={"api_key_id": key_a_viewer.key_id, "role": "TENANT_VIEWER"}, headers=admin_headers)
    client.post(f"/tenants/{tenant_b_id}/memberships", json={"api_key_id": key_b_viewer.key_id, "role": "TENANT_VIEWER"}, headers=admin_headers)

    headers_a_admin = {"Authorization": f"Bearer {raw_a_admin}"}
    headers_a_viewer = {"Authorization": f"Bearer {raw_a_viewer}"}
    headers_b_viewer = {"Authorization": f"Bearer {raw_b_viewer}"}

    # 4. Save a simple bundle under Tenant A
    n1 = ExecutionNode(node_id="node-1", step_id="1", step_name="Step 1", action_type="DETECTION", compiled_spl="failures")
    graph = ExecutionGraph(nodes=[n1], edges=[], entry_node="node-1")
    gov = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO, audit_enabled=True)
    skill = AgentSkill(name="workflow-a", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="workflow-a", created_at="2026-06-12T00:00:00Z", overall_confidence=1.0)
    bundle = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={"errors": [], "warnings": []}, traces=[])
    
    deps = get_execution_engine()
    bundle_rec = deps.bundle_store.save_bundle(
        bundle_name="bundle-a",
        skill_bundle=bundle,
        status="COMPLETED",
        created_by="admin",
        tenant_id=tenant_a_id
    )

    # 5. Start execution under Tenant A (with a password in input to verify redaction)
    trigger_resp = client.post(
        "/executions/start",
        json={"bundle_id": bundle_rec.bundle_id, "input_data": {"failures": 120, "db_password": "super-secret-pw"}},
        headers=headers_a_admin
    )
    assert trigger_resp.status_code == 200
    exec_data = trigger_resp.json()
    exec_id = exec_data["execution_id"]
    job_id = exec_data["job_id"]

    # Run background job synchronously
    queue = get_job_queue()
    worker = get_background_worker()
    job = queue.dequeue(worker.worker_id)
    assert job is not None
    worker._process_job(job)

    # 6. Retrieve results using Tenant A Viewer (Should succeed)
    res_resp = client.get(f"/executions/{exec_id}/results", headers=headers_a_viewer)
    assert res_resp.status_code == 200
    res_data = res_resp.json()
    
    assert res_data["execution_id"] == exec_id
    assert res_data["status"] == "COMPLETED"
    assert "query_executions" in res_data
    assert "action_executions" in res_data
    
    # Check that credentials were redacted
    assert res_data["context_payload"]["db_password"] == "[REDACTED]"

    # 7. Retrieve results using Tenant B Viewer (Should fail - 404/403 isolation)
    res_resp_b = client.get(f"/executions/{exec_id}/results", headers=headers_b_viewer)
    assert res_resp_b.status_code == 404 or res_resp_b.status_code == 403
