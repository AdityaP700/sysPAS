from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config.settings import settings
from app.auth.models import AuthenticatedUser, UserRole, GlobalRole, TenantRole
from app.auth.api_keys import APIKeyManager
from app.observability.request_context import set_user_id, set_role, set_tenant_id, user_id_var, role_var, tenant_id_var


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    HTTP Middleware enforcing strict API key authentication.
    Whitelists only '/health' route. Binds user details to request state and context variables.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. Whitelist check (only /health is public)
        if request.url.path == "/health":
            return await call_next(request)

        # If authentication is disabled globally, inject a system admin user and proceed
        if not settings.auth_enabled:
            system_user = AuthenticatedUser(
                user_id="system",
                tenant_id="system",
                global_role=GlobalRole.ADMIN,
                name="System Administrator (Auth Disabled)"
            )
            request.state.user = system_user
            
            token_user = set_user_id(system_user.user_id)
            token_role = set_role(system_user.role.value)
            token_tenant = set_tenant_id(system_user.tenant_id)
            try:
                return await call_next(request)
            finally:
                user_id_var.reset(token_user)
                role_var.reset(token_role)
                tenant_id_var.reset(token_tenant)

        # 2. Extract Authorization Header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or malformed Authorization header. Must be Bearer token."}
            )

        token = auth_header.split(" ", 1)[1]

        from app.web.dependencies import get_sqlite_repository
        dependency_provider = request.app.dependency_overrides.get(get_sqlite_repository, get_sqlite_repository)
        repo = dependency_provider()
        if not repo:
            return JSONResponse(
                status_code=500,
                content={"detail": "API storage repository is uninitialized."}
            )

        manager = APIKeyManager(repo)
        key_record = manager.validate_api_key(token)
        if not key_record:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid, inactive, or revoked API Key token."}
            )

        # 4. Construct AuthenticatedUser principal
        user = AuthenticatedUser(
            user_id=key_record.key_id,
            tenant_id=key_record.tenant_id,
            global_role=key_record.global_role,
            tenant_role=key_record.tenant_role,
            name=key_record.name
        )
        request.state.user = user

        # 5. Propagate caller details into structured logs request context
        token_user = set_user_id(user.user_id)
        role_val = (user.global_role.value if user.global_role else None) or (user.tenant_role.value if user.tenant_role else None) or ""
        token_role = set_role(role_val)
        token_tenant = set_tenant_id(user.tenant_id)
        try:
            return await call_next(request)
        finally:
            user_id_var.reset(token_user)
            role_var.reset(token_role)
            tenant_id_var.reset(token_tenant)
