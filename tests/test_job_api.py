import os
import tempfile
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.web.main import app
from app.config.settings import settings
from app.storage.sqlite import SQLiteRepository
from app.auth.api_keys import APIKeyManager
from app.auth.models import GlobalRole, TenantRole
from app.web.dependencies import get_sqlite_repository, get_execution_engine, get_job_queue
from app.jobs.models import JobRecord, JobStatus


@pytest.fixture
def temp_db_file():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


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
    app.dependency_overrides[deps.get_job_queue] = lambda: queue
    app.dependency_overrides[deps.get_background_worker] = lambda: worker
    app.dependency_overrides[deps.get_cron_scheduler] = lambda: scheduler
    
    yield repo
    app.dependency_overrides.clear()


def test_job_api_endpoints(setup_test_auth):
    repo = setup_test_auth
    manager = APIKeyManager(repo)

    # Create admin token
    raw_admin_token, admin_key_rec = manager.create_api_key(
        name="Global Admin",
        global_role=GlobalRole.ADMIN,
        tenant_id="system"
    )
    admin_headers = {"Authorization": f"Bearer {raw_admin_token}"}

    client = TestClient(app)

    # Register tenant
    create_tenant_resp = client.post(
        "/tenants",
        json={"name": "SOC Workspace", "slug": "soc"},
        headers=admin_headers
    )
    tenant_id = create_tenant_resp.json()["tenant_id"]

    # Create keys for tenant
    raw_op_token, op_key = manager.create_api_key(
        name="SOC Operator",
        tenant_role=TenantRole.TENANT_OPERATOR,
        tenant_id=tenant_id
    )
    op_headers = {
        "Authorization": f"Bearer {raw_op_token}",
        "X-Tenant-ID": tenant_id
    }

    # Seed a job directly in queue
    queue = get_job_queue()
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    job = JobRecord(
        job_id="job-api-test",
        tenant_id=tenant_id,
        execution_id="exec-api-test",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.QUEUED,
        created_at=now_str,
        created_by=op_key.key_id,
        priority=100
    )
    queue.enqueue(job)

    # Test GET /jobs
    resp_list = client.get("/jobs", headers=op_headers)
    assert resp_list.status_code == 200
    assert len(resp_list.json()) == 1
    assert resp_list.json()[0]["job_id"] == "job-api-test"

    # Test GET /jobs/{job_id}
    resp_get = client.get("/jobs/job-api-test", headers=op_headers)
    assert resp_get.status_code == 200
    assert resp_get.json()["status"] == "QUEUED"

    # Test POST /jobs/{job_id}/cancel
    resp_cancel = client.post("/jobs/job-api-test/cancel", headers=op_headers)
    assert resp_cancel.status_code == 200
    assert resp_cancel.json() == {"cancelled": True}

    # Verify job status changed to CANCELLED
    resp_get_after = client.get("/jobs/job-api-test", headers=op_headers)
    assert resp_get_after.json()["status"] == "CANCELLED"
