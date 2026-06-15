import pytest
import os
import tempfile
from fastapi.testclient import TestClient
from app.web.main import app
from app.storage.sqlite import SQLiteRepository
from app.audit.repository import SQLiteAuditRepository
from app.auth.models import AuthenticatedUser, GlobalRole, TenantRole, UserRole
from app.web.dependencies import require_role, require_tenant_role, resolve_tenant_id, get_sqlite_repository, get_audit_repository


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = SQLiteRepository(path)
    audit_repo = SQLiteAuditRepository(path)
    
    yield repo, audit_repo
    
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_policy_api_workflow(temp_db):
    repo, audit_repo = temp_db
    
    # 1. Setup mock user and overrides
    mock_user = AuthenticatedUser(
        user_id="admin-user",
        tenant_id="tenant-1",
        tenant_role=TenantRole.TENANT_ADMIN,
        global_role=None,
        name="Tenant Admin"
    )
    
    app.dependency_overrides[get_sqlite_repository] = lambda: repo
    app.dependency_overrides[get_audit_repository] = lambda: audit_repo
    app.dependency_overrides[resolve_tenant_id] = lambda: "tenant-1"
    app.dependency_overrides[require_tenant_role(TenantRole.TENANT_ADMIN)] = lambda: mock_user
    app.dependency_overrides[require_tenant_role(TenantRole.TENANT_VIEWER)] = lambda: mock_user
    
    client = TestClient(app)
    
    # 2. Create policy
    policy_payload = {
        "name": "Block PagerDuty",
        "policy_type": "EXECUTION",
        "priority": 150,
        "policy_definition": [
            {
                "if": {"connector_type": "PAGERDUTY"},
                "then": {"allowed": False, "message": "PagerDuty connector is denylisted"}
            }
        ]
    }
    
    response = client.post("/policies", json=policy_payload)
    assert response.status_code == 200, response.text
    res_data = response.json()
    assert res_data["name"] == "Block PagerDuty"
    assert res_data["version"] == 1
    assert res_data["is_current"] is True
    policy_id = res_data["policy_id"]
    
    # 3. List policies
    response = client.get("/policies")
    assert response.status_code == 200
    list_data = response.json()
    assert len(list_data) == 1
    assert list_data[0]["policy_id"] == policy_id
    
    # 4. Get specific policy
    response = client.get(f"/policies/{policy_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Block PagerDuty"
    
    # 5. Update policy (should increment version to 2)
    update_payload = {
        "name": "Block PagerDuty Updated",
        "enabled": True,
        "priority": 160,
        "policy_definition": [
            {
                "if": {"connector_type": "PAGERDUTY"},
                "then": {"allowed": False, "message": "PagerDuty is strictly blocked"}
            }
        ]
    }
    response = client.put(f"/policies/{policy_id}", json=update_payload)
    assert response.status_code == 200
    updated_data = response.json()
    assert updated_data["version"] == 2
    assert updated_data["name"] == "Block PagerDuty Updated"
    
    # 6. Verify version 1 still exists but is not current
    p_v1 = repo.get_policy("tenant-1", policy_id, version=1)
    assert p_v1 is not None
    
    # 7. Rollback policy back to version 1
    response = client.post(f"/policies/{policy_id}/rollback", json={"target_version": 1})
    assert response.status_code == 200
    rolled_data = response.json()
    assert rolled_data["version"] == 1
    assert rolled_data["is_current"] is True
    
    # Check updated audit logs
    audits = audit_repo.list_audit_events("tenant-1")
    actions = [a.action for a in audits]
    assert "POLICY_ROLLBACK" in actions
    
    # 8. Simulate Policy
    sim_payload = {
        "context": {"connector_type": "PAGERDUTY"},
        "policy_definition": [
            {
                "if": {"connector_type": "PAGERDUTY"},
                "then": {"allowed": False, "message": "Simulated block"}
            }
        ]
    }
    response = client.post("/policies/simulate", json=sim_payload)
    assert response.status_code == 200
    sim_res = response.json()
    assert sim_res["allowed"] is False
    assert "Simulated block" in sim_res["violations"]

    # 9. Clean up overrides
    app.dependency_overrides.clear()
