import os
import tempfile
import pytest
from fastapi import HTTPException
from app.auth.models import AuthenticatedUser, GlobalRole, TenantRole
from app.web.dependencies import require_global_admin, require_tenant_role


@pytest.fixture(autouse=True)
def setup_rbac_test(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    from app.storage.sqlite import SQLiteRepository
    repo = SQLiteRepository(path)
    monkeypatch.setattr("app.web.dependencies._repo_instance", repo)
    monkeypatch.setattr("app.web.dependencies.get_sqlite_repository", lambda: repo)
    yield repo
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_require_global_admin_dependency():
    """Verify require_global_admin allows global ADMIN and denies others."""
    admin_user = AuthenticatedUser(
        user_id="admin",
        tenant_id="system",
        global_role=GlobalRole.ADMIN,
        name="Global Admin"
    )
    operator_user = AuthenticatedUser(
        user_id="operator",
        tenant_id="system",
        tenant_role=TenantRole.TENANT_OPERATOR,
        name="Tenant Operator"
    )

    # 1. Allowed
    res = require_global_admin(admin_user)
    assert res.user_id == "admin"

    # 2. Denied
    with pytest.raises(HTTPException) as exc_info:
        require_global_admin(operator_user)
    assert exc_info.value.status_code == 403
    assert "Requires global ADMIN role" in exc_info.value.detail


class MockRequest:
    def __init__(self, path_params=None, headers=None):
        self.path_params = path_params or {}
        self.headers = headers or {}


def test_require_tenant_role_hierarchy():
    """Verify require_tenant_role enforces role hierarchy and allows global ADMIN bypass."""
    admin_user = AuthenticatedUser(
        user_id="admin",
        tenant_id="system",
        global_role=GlobalRole.ADMIN,
        name="Global Admin"
    )
    operator_user = AuthenticatedUser(
        user_id="op-1",
        tenant_id="tenant-soc",
        tenant_role=TenantRole.TENANT_OPERATOR,
        name="Tenant Operator"
    )
    viewer_user = AuthenticatedUser(
        user_id="vw-1",
        tenant_id="tenant-soc",
        tenant_role=TenantRole.TENANT_VIEWER,
        name="Tenant Viewer"
    )

    # 1. Global ADMIN always allowed regardless of tenant or role requirement
    req_admin = require_tenant_role(TenantRole.TENANT_ADMIN)
    res = req_admin(MockRequest(path_params={"tenant_id": "tenant-soc"}), admin_user)
    assert res.user_id == "admin"

    # 2. Tenant Operator requesting Tenant Operator (or lower) allowed on their home tenant
    req_operator = require_tenant_role(TenantRole.TENANT_OPERATOR)
    res = req_operator(MockRequest(path_params={"tenant_id": "tenant-soc"}), operator_user)
    assert res.user_id == "op-1"

    # 3. Tenant Viewer requesting Tenant Operator (higher privilege) denied on their home tenant
    with pytest.raises(HTTPException) as exc_info:
        req_operator(MockRequest(path_params={"tenant_id": "tenant-soc"}), viewer_user)
    assert exc_info.value.status_code == 403
    assert "Required workspace role" in exc_info.value.detail
