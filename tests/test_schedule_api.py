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
from app.web.dependencies import get_sqlite_repository, get_execution_engine


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


def test_schedule_api_crud_flow(setup_test_auth):
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

    # Test POST /schedules (requires bundle - we will bypass bundle validation in our schema if it defaults to version 1)
    # Wait, the route tries to fetch the latest bundle version if version is None.
    # To prevent 404, let's pass a specific version or create a dummy bundle.
    # Actually, we can just save a dummy bundle record in SQLite first.
    from app.package.bundle import SkillBundle
    from app.agent.graph import ExecutionGraph, ExecutionNode
    from app.agent.governance import GovernancePolicy, ExecutionMode
    from app.domain.models import AgentSkill
    from app.package.manifest import AgentSkillManifest

    n1 = ExecutionNode(node_id="n1", step_id="1", step_name="Step 1", action_type="DETECTION", compiled_spl="query")
    graph = ExecutionGraph(nodes=[n1], edges=[], entry_node="n1")
    gov = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO)
    skill = AgentSkill(name="scheduled_skill", source_runbook="sop.md", graph=graph, governance=gov, steps=[])
    manifest = AgentSkillManifest(skill_name="scheduled_skill", created_at="2026-06-12T00:00:00Z", overall_confidence=1.0)
    bundle = SkillBundle(manifest=manifest, agent_skill=skill, diagnostics={"errors": [], "warnings": []}, traces=[])
    
    deps = sys_deps = import_deps = None
    import app.web.dependencies as deps
    deps._bundle_store_instance.save_bundle("bundle-sched", bundle, "COMPLETED", op_key.key_id, tenant_id)

    # Post schedule
    resp_create = client.post(
        "/schedules",
        json={
            "bundle_id": "bundle-sched",
            "cron_expression": "*/5 * * * *",
        },
        headers=op_headers
    )
    assert resp_create.status_code == 200, resp_create.json()
    schedule_id = resp_create.json()["schedule_id"]
    assert resp_create.json()["cron_expression"] == "*/5 * * * *"
    assert resp_create.json()["enabled"] is True

    # Test GET /schedules
    resp_list = client.get("/schedules", headers=op_headers)
    assert resp_list.status_code == 200
    assert len(resp_list.json()) == 1
    assert resp_list.json()[0]["schedule_id"] == schedule_id

    # Test GET /schedules/{schedule_id}
    resp_get = client.get(f"/schedules/{schedule_id}", headers=op_headers)
    assert resp_get.status_code == 200
    assert resp_get.json()["schedule_id"] == schedule_id

    # Test DELETE /schedules/{schedule_id}
    resp_del = client.delete(f"/schedules/{schedule_id}", headers=op_headers)
    assert resp_del.status_code == 200
    assert resp_del.json() == {"deleted": True}

    # Verify deleted
    resp_get_after = client.get(f"/schedules/{schedule_id}", headers=op_headers)
    assert resp_get_after.status_code == 404
