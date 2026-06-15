import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from app.web.main import app
from app.config.settings import settings
from app.storage.sqlite import SQLiteRepository
from app.auth.api_keys import APIKeyManager
from app.auth.models import GlobalRole, TenantRole, TenantRecord, MembershipRecord
from app.web.dependencies import get_sqlite_repository


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

    # Monkeypatch the singleton instances in dependencies
    import app.web.dependencies as deps
    monkeypatch.setattr(deps, "_repo_instance", repo)
    monkeypatch.setattr(deps, "_bundle_store_instance", deps.BundleStore(repo))
    monkeypatch.setattr(deps, "_compilation_store_instance", deps.CompilationStore(repo))
    monkeypatch.setattr(deps, "_trace_store_instance", deps.TraceStore(repo))
    monkeypatch.setattr(deps, "_audit_repo_instance", deps.SQLiteAuditRepository(temp_db_file))
    
    # Also update the service instance
    monkeypatch.setattr(deps, "_service_instance", deps.RunbookService(
        repo=repo,
        bundle_store=deps._bundle_store_instance,
        compilation_store=deps._compilation_store_instance,
        trace_store=deps._trace_store_instance,
    ))

    app.dependency_overrides[get_sqlite_repository] = lambda: repo
    yield repo
    app.dependency_overrides.clear()


def test_tenant_and_membership_api_flow(setup_test_auth):
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

    # 2. Register a new tenant workspace
    create_tenant_resp = client.post(
        "/tenants",
        json={"name": "SOC Team", "slug": "soc"},
        headers=admin_headers
    )
    assert create_tenant_resp.status_code == 200
    tenant_data = create_tenant_resp.json()
    assert tenant_data["name"] == "SOC Team"
    assert tenant_data["slug"] == "soc"
    tenant_id = tenant_data["tenant_id"]

    # 3. Create a key in the new tenant
    raw_tenant_token, tenant_key_rec = manager.create_api_key(
        name="SOC Admin Key",
        tenant_role=TenantRole.TENANT_ADMIN,
        tenant_id=tenant_id
    )
    tenant_headers = {"Authorization": f"Bearer {raw_tenant_token}"}

    # 4. Map a membership for the SOC key in the new tenant
    member_resp = client.post(
        f"/tenants/{tenant_id}/memberships",
        json={"api_key_id": tenant_key_rec.key_id, "role": "TENANT_ADMIN"},
        headers=tenant_headers
    )
    assert member_resp.status_code == 200

    # 5. Verify the membership is listed
    list_members_resp = client.get(
        f"/tenants/{tenant_id}/memberships",
        headers=tenant_headers
    )
    assert list_members_resp.status_code == 200
    members = list_members_resp.json()
    assert len(members) == 1
    assert members[0]["api_key_id"] == tenant_key_rec.key_id

    # 6. Verify cross-tenant isolation: SOC Admin Key cannot access the system tenant's logs
    cross_logs_resp = client.get(
        "/audit/logs",
        headers={**tenant_headers, "X-Tenant-ID": "system"}
    )
    # The routes check resolve_tenant_id which denies access to non-members
    assert cross_logs_resp.status_code == 403
