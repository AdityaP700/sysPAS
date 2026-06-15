from fastapi import HTTPException
from app.auth.models import AuthenticatedUser, UserRole, GlobalRole

ROLE_values = {
    UserRole.VIEWER: 1,
    UserRole.OPERATOR: 2,
    UserRole.ADMIN: 3,
}


def has_role_privilege(user_role: UserRole, required_role: UserRole) -> bool:
    """Evaluate whether the user's role satisfies the required privilege level."""
    return ROLE_values.get(user_role, 0) >= ROLE_values.get(required_role, 0)


def check_bundle_ownership(current_user: AuthenticatedUser, owner_id: str) -> None:
    """
    Enforce ownership checks.
    Allow execution if the current user is a global ADMIN or if they are the resource creator.
    """
    if current_user.global_role == GlobalRole.ADMIN or current_user.role == UserRole.ADMIN:
        return
    if current_user.user_id == owner_id:
        return
    raise HTTPException(
        status_code=403,
        detail="Permission denied: You do not own this resource and lack administrator privileges."
    )
