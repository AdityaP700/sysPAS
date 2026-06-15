import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.web.main import app
from app.config.settings import settings
from app.storage.sqlite import SQLiteRepository
from app.auth.api_keys import APIKeyManager
from app.auth.models import UserRole
from app.web.dependencies import get_sqlite_repository


@pytest.fixture
def temp_db_file():
    """Create a temporary database file and clean it up after the test completes."""
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
    """Setup isolated test configuration settings and dependency overrides."""
    repo = SQLiteRepository(temp_db_file)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "default_admin_api_key", "rm_key_test_admin_secret_key_123")
    monkeypatch.setattr(settings, "sqlite_db_path", temp_db_file)

    app.dependency_overrides[get_sqlite_repository] = lambda: repo

    yield repo

    app.dependency_overrides.clear()


def test_public_route_bypass():
    """Verify that whitelisted public routes like /health bypass authentication check."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_middleware_missing_token():
    """Verify that requests missing authorization tokens are blocked with 401 Unauthorized status."""
    client = TestClient(app)
    response = client.get("/bundles")
    assert response.status_code == 401
    assert "Missing or malformed Authorization header" in response.json()["detail"]


def test_middleware_malformed_token():
    """Verify that malformed headers (not starting with Bearer) are blocked with 401."""
    client = TestClient(app)
    response = client.get("/bundles", headers={"Authorization": "rm_key_some_plaintext"})
    assert response.status_code == 401
    assert "Bearer token" in response.json()["detail"]


def test_middleware_invalid_token():
    """Verify that invalid tokens fail validation check with 401."""
    client = TestClient(app)
    response = client.get("/bundles", headers={"Authorization": "Bearer rm_key_nonexistent"})
    assert response.status_code == 401
    assert "Invalid, inactive, or revoked" in response.json()["detail"]


def test_middleware_revoked_disabled_token(setup_test_auth):
    """Verify that disabled or revoked keys block API requests with 401."""
    repo = setup_test_auth
    manager = APIKeyManager(repo)

    # Create key
    raw_token, record = manager.create_api_key("Viewer Key", UserRole.VIEWER)

    # Verify key initially works
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {raw_token}"}
    response_active = client.get("/bundles", headers=headers)
    assert response_active.status_code == 200

    # Revoke key
    repo.revoke_api_key(record.key_id)

    # Request should now be blocked
    response_revoked = client.get("/bundles", headers=headers)
    assert response_revoked.status_code == 401
    assert "Invalid, inactive, or revoked" in response_revoked.json()["detail"]


def test_middleware_valid_token_access(setup_test_auth):
    """Verify that valid tokens authorize and process requests successfully."""
    repo = setup_test_auth
    manager = APIKeyManager(repo)

    # 1. Create a VIEWER key
    raw_token, _ = manager.create_api_key("Viewer Key", UserRole.VIEWER)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {raw_token}"}

    # Access compile endpoint (requires VIEWER or higher)
    runbook_md = (
        "# API Persist Runbook\n"
        "## Steps\n"
        "1. Check auth failures [DETECTION] {data_source=auth_logs}\n"
    )
    payload = {
        "content": runbook_md,
        "filename": "api_persist.md",
    }
    compile_resp = client.post("/compile", json=payload, headers=headers)
    assert compile_resp.status_code == 200
    assert compile_resp.json()["status"] == "SUCCESS"
