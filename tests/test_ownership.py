import pytest
from fastapi import HTTPException
from app.auth.models import AuthenticatedUser, UserRole
from app.auth.permissions import check_bundle_ownership


def test_ownership_enforcement():
    """Verify ownership rules: Admin has master access; Operators can access own resources; unauthorized users fail."""
    admin_user = AuthenticatedUser(user_id="admin_key", role=UserRole.ADMIN, name="Admin")
    operator_a = AuthenticatedUser(user_id="operator_a_key", role=UserRole.OPERATOR, name="Operator A")
    operator_b = AuthenticatedUser(user_id="operator_b_key", role=UserRole.OPERATOR, name="Operator B")

    # 1. Admin checks (should always succeed regardless of resource owner)
    check_bundle_ownership(admin_user, owner_id="operator_a_key")
    check_bundle_ownership(admin_user, owner_id="system")

    # 2. Operator checking own resources (should succeed)
    check_bundle_ownership(operator_a, owner_id="operator_a_key")
    check_bundle_ownership(operator_b, owner_id="operator_b_key")

    # 3. Operator checking another operator's resources (should fail with 403)
    with pytest.raises(HTTPException) as exc_info:
        check_bundle_ownership(operator_a, owner_id="operator_b_key")
    assert exc_info.value.status_code == 403
    assert "Permission denied" in exc_info.value.detail

    # 4. Operator checking 'system' resources (should fail with 403)
    with pytest.raises(HTTPException) as exc_info:
        check_bundle_ownership(operator_b, owner_id="system")
    assert exc_info.value.status_code == 403
