from typing import Optional
from fastapi import Request, HTTPException, Depends

from app.config.settings import settings
from app.service.runbook_service import RunbookService
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.storage.compilation_store import CompilationStore
from app.storage.trace_store import TraceStore

from app.audit.repository import SQLiteAuditRepository
from app.auth.models import AuthenticatedUser, UserRole, GlobalRole, TenantRole
from app.auth.permissions import has_role_privilege

from app.runtime.runner import BaseQueryRunner, MockQueryRunner, SplunkQueryRunner
from app.runtime.engine import ExecutionEngine

from app.jobs.queue import JobQueue
from app.jobs.worker import BackgroundWorker
from app.jobs.scheduler import CronScheduler

# Singleton instances
_repo_instance: Optional[SQLiteRepository] = None
_bundle_store_instance: Optional[BundleStore] = None
_compilation_store_instance: Optional[CompilationStore] = None
_trace_store_instance: Optional[TraceStore] = None
_audit_repo_instance: Optional[SQLiteAuditRepository] = None
_query_runner_instance: Optional[BaseQueryRunner] = None
_execution_engine_instance: Optional[ExecutionEngine] = None
_job_queue_instance: Optional[JobQueue] = None
_worker_instance: Optional[BackgroundWorker] = None
_scheduler_instance: Optional[CronScheduler] = None

if settings.storage_enabled:
    _repo_instance = SQLiteRepository(settings.sqlite_db_path)
    _bundle_store_instance = BundleStore(_repo_instance)
    _compilation_store_instance = CompilationStore(_repo_instance)
    _trace_store_instance = TraceStore(_repo_instance)
    _audit_repo_instance = SQLiteAuditRepository(settings.sqlite_db_path)
    _query_runner_instance = (
        SplunkQueryRunner(repo=_repo_instance, audit_repo=_audit_repo_instance)
        if settings.enable_mcp
        else MockQueryRunner()
    )
    _execution_engine_instance = ExecutionEngine(
        repo=_repo_instance,
        bundle_store=_bundle_store_instance,
        audit_repo=_audit_repo_instance,
        query_runner=_query_runner_instance,
    )
    _job_queue_instance = JobQueue(_repo_instance)
    _worker_instance = BackgroundWorker(
        queue=_job_queue_instance,
        engine=_execution_engine_instance
    )
    _scheduler_instance = CronScheduler(
        repo=_repo_instance,
        queue=_job_queue_instance
    )
    # Register callbacks with metrics_collector
    from app.observability.metrics import metrics_collector
    metrics_collector.register_queue_callbacks(
        queue_depth_cb=_job_queue_instance.get_queue_depth,
        oldest_job_age_cb=_job_queue_instance.get_oldest_queued_job_age
    )

from app.splunk.adapters.mcp_generator import SplunkMCPGenerator
from app.splunk.adapters.mcp_optimizer import SplunkMCPOptimizer
from app.splunk.adapters.mcp_explainer import SplunkMCPExplainer
from app.schema.discovery import SchemaDiscoveryEngine


def _build_mcp_adapters():
    """Returns real MCP adapter instances when MCP is enabled; None otherwise (RunbookService falls back to mocks)."""
    if settings.enable_mcp:
        return (
            SplunkMCPGenerator(),
            SplunkMCPOptimizer(),
            SplunkMCPExplainer(),
            SchemaDiscoveryEngine(),
        )
    return None, None, None, None


_gen, _opt, _exp, _schema = _build_mcp_adapters()

# The service singleton receives the shared database and store services
_service_instance = RunbookService(
    repo=_repo_instance,
    bundle_store=_bundle_store_instance,
    compilation_store=_compilation_store_instance,
    trace_store=_trace_store_instance,
    generator=_gen,
    optimizer=_opt,
    explainer=_exp,
    schema_provider=_schema,
)


def get_runbook_service() -> RunbookService:
    """Dependency provider for RunbookService singleton."""
    return _service_instance


def get_query_runner() -> Optional[BaseQueryRunner]:
    """Dependency provider for BaseQueryRunner singleton."""
    return _query_runner_instance


def get_execution_engine() -> Optional[ExecutionEngine]:
    """Dependency provider for ExecutionEngine singleton."""
    return _execution_engine_instance


def get_sqlite_repository() -> Optional[SQLiteRepository]:
    """Dependency provider for SQLiteRepository (which implements APIKeyRepository)."""
    return _repo_instance


def get_bundle_store() -> Optional[BundleStore]:
    """Dependency provider for BundleStore singleton."""
    return _bundle_store_instance


def get_compilation_store() -> Optional[CompilationStore]:
    """Dependency provider for CompilationStore singleton."""
    return _compilation_store_instance


def get_trace_store() -> Optional[TraceStore]:
    """Dependency provider for TraceStore singleton."""
    return _trace_store_instance


def get_audit_repository() -> Optional[SQLiteAuditRepository]:
    """Dependency provider for SQLiteAuditRepository singleton."""
    return _audit_repo_instance


def get_current_user(request: Request) -> AuthenticatedUser:
    """
    Dependency to retrieve the currently authenticated user principal.
    If auth is globally disabled, a system administrator stub is returned.
    """
    user = getattr(request.state, "user", None)
    if not user:
        if settings.auth_enabled:
            raise HTTPException(status_code=401, detail="Authentication credentials not found.")
        # Fallback if auth_enabled is False
        return AuthenticatedUser(
            user_id="system",
            tenant_id="system",
            global_role=GlobalRole.ADMIN,
            name="System Administrator (Auth Disabled)"
        )
    return user


def require_role(required_role: UserRole):
    """
    FastAPI dependency factory enforcing a minimum user privilege role.
    Verifies the user's role satisfies the privilege hierarchy.
    """
    def dependency(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if not has_role_privilege(current_user.role, required_role):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied. Required privilege level: {required_role.value}."
            )
        return current_user
    return dependency


TENANT_ROLE_VALUES = {
    TenantRole.TENANT_VIEWER: 1,
    TenantRole.TENANT_OPERATOR: 2,
    TenantRole.TENANT_ADMIN: 3,
}


def require_global_admin(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Enforces that the caller is a global ADMIN."""
    if current_user.global_role != GlobalRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Permission denied: Requires global ADMIN role."
        )
    return current_user


def require_tenant_role(required_role: TenantRole):
    """
    FastAPI dependency factory enforcing a minimum tenant privilege role.
    If the caller is a global ADMIN, they bypass all checks.
    Otherwise, verifies membership and tenant role hierarchy.
    """
    def dependency(
        request: Request,
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        # Extract target tenant from path or header
        tenant_id = request.path_params.get("tenant_id")
        x_tenant_id = request.headers.get("X-Tenant-ID")
        target_tenant = x_tenant_id or tenant_id or current_user.tenant_id

        # 1. Global ADMIN bypasses all tenant checks
        if current_user.global_role == GlobalRole.ADMIN:
            return current_user

        # 2. Check if this is the user's home tenant and they have sufficient role
        if target_tenant == current_user.tenant_id:
            user_role = current_user.tenant_role
            if user_role and TENANT_ROLE_VALUES.get(user_role, 0) >= TENANT_ROLE_VALUES.get(required_role, 0):
                return current_user

        # 3. Check membership table in database for other memberships
        repo = get_sqlite_repository()
        if repo:
            memberships = repo.get_memberships(target_tenant)
            for m in memberships:
                if m.api_key_id == current_user.user_id:
                    if TENANT_ROLE_VALUES.get(m.role, 0) >= TENANT_ROLE_VALUES.get(required_role, 0):
                        # Temporarily update tenant_role on current_user context
                        current_user.tenant_role = m.role
                        return current_user

        raise HTTPException(
            status_code=403,
            detail=f"Permission denied: Required workspace role: {required_role.value} in tenant '{target_tenant}'."
        )
    return dependency


def resolve_tenant_id(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user)
) -> str:
    """
    Resolves the active tenant_id for the request context.
    For global ADMINs, respects the X-Tenant-ID header or path parameter.
    For others, resolves using path or user home tenant.
    """
    tenant_id = request.path_params.get("tenant_id")
    x_tenant_id = request.headers.get("X-Tenant-ID")
    target_tenant = x_tenant_id or tenant_id or current_user.tenant_id

    # 1. Global ADMIN allows any target tenant
    if current_user.global_role == GlobalRole.ADMIN:
        return target_tenant

    # 2. Non-admin must be a member of the target tenant
    if target_tenant != current_user.tenant_id:
        repo = get_sqlite_repository()
        if repo:
            memberships = repo.get_memberships(target_tenant)
            is_member = any(m.api_key_id == current_user.user_id for m in memberships)
            if not is_member:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: You are not a member of this tenant workspace."
                )
    return target_tenant


def get_job_queue() -> Optional[JobQueue]:
    """Dependency provider for JobQueue singleton."""
    return _job_queue_instance


def get_background_worker() -> Optional[BackgroundWorker]:
    """Dependency provider for BackgroundWorker singleton."""
    return _worker_instance


def get_cron_scheduler() -> Optional[CronScheduler]:
    """Dependency provider for CronScheduler singleton."""
    return _scheduler_instance
