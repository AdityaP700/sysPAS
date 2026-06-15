import contextvars
from typing import Optional

request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("correlation_id", default=None)


def set_request_id(request_id: str) -> contextvars.Token:
    """Set the active request ID context variable."""
    return request_id_var.set(request_id)


def get_request_id() -> Optional[str]:
    """Retrieve the current active request ID from the context."""
    return request_id_var.get()


def set_correlation_id(correlation_id: str) -> contextvars.Token:
    """Set the active correlation ID context variable."""
    return correlation_id_var.set(correlation_id)


def get_correlation_id() -> Optional[str]:
    """Retrieve the current active correlation ID from the context."""
    return correlation_id_var.get()


user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("user_id", default=None)
role_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("role", default=None)


def set_user_id(user_id: str) -> contextvars.Token:
    """Set the active authenticated user ID context variable."""
    return user_id_var.set(user_id)


def get_user_id() -> Optional[str]:
    """Retrieve the current active user ID from the context."""
    return user_id_var.get()


def set_role(role: str) -> contextvars.Token:
    """Set the active authenticated user role context variable."""
    return role_var.set(role)


def get_role() -> Optional[str]:
    """Retrieve the current active user role from the context."""
    return role_var.get()


tenant_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tenant_id", default=None)


def set_tenant_id(tenant_id: str) -> contextvars.Token:
    """Set the active tenant ID context variable."""
    return tenant_id_var.set(tenant_id)


def get_tenant_id() -> Optional[str]:
    """Retrieve the current active tenant ID from the context."""
    return tenant_id_var.get()


