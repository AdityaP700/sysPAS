import pytest
import os
import tempfile
from fastapi.testclient import TestClient
from app.web.main import app
from app.storage.sqlite import SQLiteRepository
from app.audit.repository import SQLiteAuditRepository
from app.auth.models import AuthenticatedUser, GlobalRole, TenantRole
from app.web.dependencies import require_tenant_role, resolve_tenant_id, get_sqlite_repository, get_audit_repository
from app.vault.models import SecretType
from app.config.settings import settings


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


def test_secret_api_endpoints(temp_db):
    repo, audit_repo = temp_db
    
    # 1. Setup dependency overrides
    mock_user = AuthenticatedUser(
        user_id="user-admin",
        tenant_id="tenant-1",
        tenant_role=TenantRole.TENANT_ADMIN,
        name="Tenant Administrator"
    )
    
    app.dependency_overrides[require_tenant_role(TenantRole.TENANT_ADMIN)] = lambda: mock_user
    app.dependency_overrides[resolve_tenant_id] = lambda: "tenant-1"
    app.dependency_overrides[get_sqlite_repository] = lambda: repo
    app.dependency_overrides[get_audit_repository] = lambda: audit_repo
    
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    client = TestClient(app)
    
    try:
        # Create a secret
        payload = {
            "name": "api_key_prod",
            "secret_type": "API_KEY",
            "value": "secret-plaintext-value"
        }
        response = client.post("/vault/secrets", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "api_key_prod"
        assert data["version"] == 1
        assert "value" not in data # Plaintext should not be in metadata response
        assert "encrypted_value" not in data # Encrypted value should not be in metadata response
        
        secret_id = data["secret_id"]
        
        # List secrets
        list_response = client.get("/vault/secrets")
        assert list_response.status_code == 200
        secrets_list = list_response.json()
        assert len(secrets_list) == 1
        assert secrets_list[0]["secret_id"] == secret_id
        
        # Get specific secret
        get_response = client.get(f"/vault/secrets/{secret_id}")
        assert get_response.status_code == 200
        assert get_response.json()["name"] == "api_key_prod"
        
        # Rotate secret
        rotate_payload = {"value": "new-plaintext-value"}
        rotate_response = client.post(f"/vault/secrets/{secret_id}/rotate", json=rotate_payload)
        assert rotate_response.status_code == 200
        rotated_data = rotate_response.json()
        assert rotated_data["version"] == 2
        assert rotated_data["is_current"] is True
        
        # Disable/delete secret
        delete_response = client.delete(f"/vault/secrets/{secret_id}")
        assert delete_response.status_code == 200
        assert delete_response.json() == {"disabled": True}
        
    finally:
        # Clean up dependency overrides
        app.dependency_overrides.clear()
        settings.vault_master_key = old_key
