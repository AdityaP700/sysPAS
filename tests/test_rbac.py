import pytest
from fastapi import HTTPException
from app.auth.models import AuthenticatedUser, UserRole
from app.auth.permissions import has_role_privilege
from app.web.dependencies import require_role


def test_role_privilege_hierarchy():
    """Verify role privilege levels satisfy has_role_privilege checks."""
    assert has_role_privilege(UserRole.ADMIN, UserRole.VIEWER) is True
    assert has_role_privilege(UserRole.ADMIN, UserRole.OPERATOR) is True
    assert has_role_privilege(UserRole.ADMIN, UserRole.ADMIN) is True

    assert has_role_privilege(UserRole.OPERATOR, UserRole.VIEWER) is True
    assert has_role_privilege(UserRole.OPERATOR, UserRole.OPERATOR) is True
    assert has_role_privilege(UserRole.OPERATOR, UserRole.ADMIN) is False

    assert has_role_privilege(UserRole.VIEWER, UserRole.VIEWER) is True
    assert has_role_privilege(UserRole.VIEWER, UserRole.OPERATOR) is False
    assert has_role_privilege(UserRole.VIEWER, UserRole.ADMIN) is False


def test_require_role_dependency_checks():
    """Verify that require_role dependency factory checks role permissions and raises 403 when deficient."""
    viewer_user = AuthenticatedUser(user_id="viewer_1", role=UserRole.VIEWER, name="Viewer User")
    operator_user = AuthenticatedUser(user_id="operator_1", role=UserRole.OPERATOR, name="Operator User")

    # 1. Operators require Operator or lower
    dependency_operator = require_role(UserRole.OPERATOR)
    
    # Passing operator should succeed
    allowed_operator = dependency_operator(operator_user)
    assert allowed_operator.user_id == "operator_1"

    # Passing viewer to operator endpoint should raise 403
    with pytest.raises(HTTPException) as exc_info:
        dependency_operator(viewer_user)
    assert exc_info.value.status_code == 403
    assert "Permission denied" in exc_info.value.detail

    # 2. Admins require Admin or lower
    dependency_admin = require_role(UserRole.ADMIN)
    with pytest.raises(HTTPException) as exc_info:
        dependency_admin(operator_user)
    assert exc_info.value.status_code == 403
