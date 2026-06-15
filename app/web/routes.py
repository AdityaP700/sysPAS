import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.schemas import CompileRunbookResponse, SkillBundleResponse
from app.package.bundle import SkillBundle
from app.package.exporter import SkillExporter
from app.service.runbook_service import RunbookService
from app.tracing.models import CompilationTrace
from app.observability.metrics import metrics_collector
from app.observability.logging import logger

from app.storage.models import BundleRecord, CompilationRecord
from app.storage.bundle_store import BundleStore
from app.storage.compilation_store import CompilationStore
from app.storage.trace_store import TraceStore
from app.web.dependencies import (
    get_runbook_service,
    get_bundle_store,
    get_compilation_store,
    get_trace_store,
    get_sqlite_repository,
    get_audit_repository,
    get_current_user,
    require_role,
    require_global_admin,
    require_tenant_role,
    resolve_tenant_id,
    get_execution_engine,
)

from app.auth.models import AuthenticatedUser, UserRole, GlobalRole, TenantRole, TenantRecord, MembershipRecord
from app.auth.api_keys import APIKeyManager
from app.audit.models import AuditEventRecord
from app.audit.repository import SQLiteAuditRepository
from app.api.auth_schemas import (
    CreateAPIKeyRequest,
    CreateAPIKeyResponse,
    APIKeyInfo,
    TenantCreate,
    TenantResponse,
    MembershipCreate,
    MembershipResponse,
    TriggerExecutionRequest,
    ResumeExecutionRequest,
    JobStartResponse,
    ScheduleCreateRequest,
    ScheduleResponse,
)

from app.runtime.models import ExecutionRecord, NodeExecutionRecord, ApprovalRecord, ApprovalStatus, ExecutionStatus
from app.runtime.engine import ExecutionEngine
from app.jobs.models import JobRecord, JobStatus, ScheduleRecord
from app.jobs.queue import JobQueue
from app.jobs.scheduler import CronScheduler
from app.web.dependencies import get_job_queue, get_cron_scheduler

router = APIRouter()


class CompileRequest(BaseModel):
    """Input payload representing a runbook parsing and compilation request."""
    content: str = Field(..., description="Markdown or plain text runbook content")
    filename: str = Field(..., description="Runbook source filename (e.g. 'auth_check.md')")


def log_audit(
    audit_repo: Optional[SQLiteAuditRepository],
    action: str,
    resource_type: str,
    resource_id: Optional[str],
    status: str,
    user: AuthenticatedUser,
    details: Optional[dict] = None,
    tenant_id: str = "system",
) -> None:
    """Helper method to construct and persist a structured AuditEventRecord."""
    if not audit_repo:
        return
    from app.observability.request_context import get_request_id, get_correlation_id
    record = AuditEventRecord(
        audit_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        request_id=get_request_id(),
        correlation_id=get_correlation_id(),
        user_id=user.user_id,
        role=user.role.value,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        status=status,
        details=details or {},
        tenant_id=tenant_id,
    )
    audit_repo.save_audit_event(tenant_id, record)


def check_tenant_bundle_ownership(current_user: AuthenticatedUser, tenant_id: str, creator_id: str) -> None:
    """Enforce ownership checks under multi-tenant scoping."""
    if current_user.global_role == GlobalRole.ADMIN:
        return
    if current_user.tenant_id == tenant_id and current_user.tenant_role == TenantRole.TENANT_ADMIN:
        return
    if current_user.user_id == creator_id:
        return
    raise HTTPException(
        status_code=403,
        detail="Permission denied: You do not own this resource and lack administrator privileges."
    )


@router.post("/compile", response_model=CompileRunbookResponse)
def compile_runbook(
    request: CompileRequest,
    service: RunbookService = Depends(get_runbook_service),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> CompileRunbookResponse:
    """Parses, compiles, and packages a runbook configuration, recording telemetry metrics."""
    start_time = time.perf_counter()
    status = "FAILED"
    try:
        response = service.compile_runbook(
            content=request.content,
            filename=request.filename,
            owner_id=current_user.user_id,
            tenant_id=tenant_id
        )
        status = response.status
        success = response.status in ("SUCCESS", "PARTIAL")
        
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        metrics_collector.record_compilation(success=success, duration_ms=duration_ms, tenant_id=tenant_id)
        
        logger.info(
            f"Runbook compiled: {request.filename} - Status: {response.status}",
            extra={
                "component": "compiler",
                "operation": "compile_runbook",
                "duration_ms": round(duration_ms, 2),
                "status": "success" if success else "failed",
                "tenant_id": tenant_id
            }
        )
        
        log_audit(
            audit_repo=audit_repo,
            action="COMPILE_RUNBOOK",
            resource_type="runbook",
            resource_id=response.runbook_name,
            status="SUCCESS" if success else "FAILED",
            user=current_user,
            details={"filename": request.filename},
            tenant_id=tenant_id
        )
        return response
    except Exception as e:
        log_audit(
            audit_repo=audit_repo,
            action="COMPILE_RUNBOOK",
            resource_type="runbook",
            resource_id=request.filename,
            status="FAILED",
            user=current_user,
            details={"error": str(e)},
            tenant_id=tenant_id
        )
        raise e


@router.post("/bundle/export")
def export_bundle(
    bundle: SkillBundle,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> Response:
    """Exports a SkillBundle as a sorted, deterministic JSON payload."""
    try:
        exported_json = SkillExporter.export_json(bundle)
        log_audit(
            audit_repo=audit_repo,
            action="EXPORT_BUNDLE",
            resource_type="bundle",
            resource_id=bundle.manifest.skill_name,
            status="SUCCESS",
            user=current_user,
            tenant_id=tenant_id
        )
        return Response(content=exported_json, media_type="application/json")
    except Exception as e:
        log_audit(
            audit_repo=audit_repo,
            action="EXPORT_BUNDLE",
            resource_type="bundle",
            resource_id=bundle.manifest.skill_name,
            status="FAILED",
            user=current_user,
            details={"error": str(e)},
            tenant_id=tenant_id
        )
        raise e


@router.get("/health")
def health() -> dict:
    """Simple API health check endpoint (exempt from authentication)."""
    return {"status": "ok"}


# --- Tenant and Membership Administration Endpoints ---

@router.post("/tenants", response_model=TenantResponse)
def create_tenant(
    request: TenantCreate,
    current_user: AuthenticatedUser = Depends(require_global_admin),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> TenantResponse:
    """Register a new active tenant workspace. (Global ADMIN only)"""
    if not repo:
        raise HTTPException(status_code=500, detail="Storage repository is uninitialized.")
    
    tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"
    record = TenantRecord(
        tenant_id=tenant_id,
        name=request.name,
        slug=request.slug,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        enabled=True,
        deleted_at=None
    )
    try:
        repo.save_tenant(record)
        log_audit(
            audit_repo=audit_repo,
            action="CREATE_TENANT",
            resource_type="tenant",
            resource_id=tenant_id,
            status="SUCCESS",
            user=current_user,
            details={"name": request.name, "slug": request.slug},
            tenant_id="system"
        )
        return TenantResponse(
            tenant_id=record.tenant_id,
            name=record.name,
            slug=record.slug,
            created_at=record.created_at,
            enabled=record.enabled
        )
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail=f"Tenant slug '{request.slug}' already exists.")
        raise HTTPException(status_code=500, detail=f"Failed to save tenant: {str(e)}")


@router.get("/tenants", response_model=List[TenantResponse])
def list_tenants(
    current_user: AuthenticatedUser = Depends(require_global_admin),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> List[TenantResponse]:
    """List all active, non-deleted tenant workspaces. (Global ADMIN only)"""
    if not repo:
        raise HTTPException(status_code=500, detail="Storage repository is uninitialized.")
    
    tenants = repo.list_tenants()
    log_audit(
        audit_repo=audit_repo,
        action="LIST_TENANTS",
        resource_type="tenant",
        resource_id=None,
        status="SUCCESS",
        user=current_user,
        tenant_id="system"
    )
    return [
        TenantResponse(
            tenant_id=t.tenant_id,
            name=t.name,
            slug=t.slug,
            created_at=t.created_at,
            enabled=t.enabled
        ) for t in tenants
    ]


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
def get_tenant(
    tenant_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> TenantResponse:
    """Retrieve tenant details by identifier."""
    if not repo:
        raise HTTPException(status_code=500, detail="Storage repository is uninitialized.")
    
    tenant = repo.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant workspace '{tenant_id}' not found.")
    
    log_audit(
        audit_repo=audit_repo,
        action="VIEW_TENANT",
        resource_type="tenant",
        resource_id=tenant_id,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    return TenantResponse(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        slug=tenant.slug,
        created_at=tenant.created_at,
        enabled=tenant.enabled
    )


@router.delete("/tenants/{tenant_id}")
def delete_tenant(
    tenant_id: str,
    current_user: AuthenticatedUser = Depends(require_global_admin),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> dict:
    """Soft delete a tenant workspace organization. (Global ADMIN only)"""
    if not repo:
        raise HTTPException(status_code=500, detail="Storage repository is uninitialized.")
    
    success = repo.delete_tenant(tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Tenant workspace '{tenant_id}' not found.")
    
    log_audit(
        audit_repo=audit_repo,
        action="DELETE_TENANT",
        resource_type="tenant",
        resource_id=tenant_id,
        status="SUCCESS",
        user=current_user,
        tenant_id="system"
    )
    return {"deleted": True}


@router.post("/tenants/{tenant_id}/memberships", response_model=MembershipResponse)
def add_membership(
    tenant_id: str,
    request: MembershipCreate,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> MembershipResponse:
    """Map an API Key principal to a tenant role. (ADMIN or Tenant Admin only)"""
    if not repo:
        raise HTTPException(status_code=500, detail="Storage repository is uninitialized.")
    
    # Check if target key exists
    key = repo.get_api_key_by_id("system", request.api_key_id) or repo.get_api_key_by_id(tenant_id, request.api_key_id)
    if not key:
        # Check globally
        keys = repo.list_api_keys(tenant_id) + repo.list_api_keys("system")
        key = next((k for k in keys if k.key_id == request.api_key_id), None)
        if not key:
            raise HTTPException(status_code=404, detail=f"API Key '{request.api_key_id}' not found.")
            
    membership_id = f"member_{uuid.uuid4().hex[:12]}"
    record = MembershipRecord(
        membership_id=membership_id,
        tenant_id=tenant_id,
        api_key_id=request.api_key_id,
        role=request.role
    )
    try:
        repo.save_membership(record)
        log_audit(
            audit_repo=audit_repo,
            action="CREATE_MEMBERSHIP",
            resource_type="membership",
            resource_id=membership_id,
            status="SUCCESS",
            user=current_user,
            details={"api_key_id": request.api_key_id, "role": request.role.value},
            tenant_id=tenant_id
        )
        return MembershipResponse(
            membership_id=record.membership_id,
            tenant_id=record.tenant_id,
            api_key_id=record.api_key_id,
            role=record.role
        )
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="This API key already has a membership in this tenant workspace.")
        raise HTTPException(status_code=500, detail=f"Failed to map membership: {str(e)}")


@router.get("/tenants/{tenant_id}/memberships", response_model=List[MembershipResponse])
def list_memberships(
    tenant_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> List[MembershipResponse]:
    """List all API Key memberships in a tenant workspace. (ADMIN or Tenant Admin only)"""
    if not repo:
        raise HTTPException(status_code=500, detail="Storage repository is uninitialized.")
    
    memberships = repo.get_memberships(tenant_id)
    log_audit(
        audit_repo=audit_repo,
        action="LIST_MEMBERSHIPS",
        resource_type="membership",
        resource_id=None,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    return [
        MembershipResponse(
            membership_id=m.membership_id,
            tenant_id=m.tenant_id,
            api_key_id=m.api_key_id,
            role=m.role
        ) for m in memberships
    ]


@router.delete("/tenants/{tenant_id}/memberships/{membership_id}")
def delete_membership(
    tenant_id: str,
    membership_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> dict:
    """Remove a membership mapping from the tenant workspace. (ADMIN or Tenant Admin only)"""
    if not repo:
        raise HTTPException(status_code=500, detail="Storage repository is uninitialized.")
    
    success = repo.delete_membership(tenant_id, membership_id)
    if not success:
        raise HTTPException(status_code=404, detail="Membership mapping not found.")
        
    log_audit(
        audit_repo=audit_repo,
        action="DELETE_MEMBERSHIP",
        resource_type="membership",
        resource_id=membership_id,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    return {"deleted": True}


# --- Scoped Persistence Endpoints ---

@router.get("/bundles", response_model=List[BundleRecord])
def list_bundles(
    bundle_store: Optional[BundleStore] = Depends(get_bundle_store),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> List[BundleRecord]:
    """Retrieve the latest version of all unique SkillBundles stored under the resolved tenant."""
    if not bundle_store:
        raise HTTPException(status_code=400, detail="Persistence storage is disabled")
    
    bundles = bundle_store.list_bundles(tenant_id=tenant_id)
    log_audit(
        audit_repo=audit_repo,
        action="LIST_BUNDLES",
        resource_type="bundle",
        resource_id=None,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id,
    )
    return bundles


@router.get("/bundles/{bundle_id}", response_model=SkillBundleResponse)
def get_bundle(
    bundle_id: str,
    version: Optional[int] = None,
    bundle_store: Optional[BundleStore] = Depends(get_bundle_store),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> SkillBundleResponse:
    """Retrieve a specific bundle version payload, defaulting to latest if version is omitted."""
    if not bundle_store:
        raise HTTPException(status_code=400, detail="Persistence storage is disabled")
    
    record = bundle_store.get_bundle(bundle_id, version, tenant_id=tenant_id)
    if not record:
        detail = f"Bundle '{bundle_id}' not found"
        if version is not None:
            detail += f" for version {version}"
        log_audit(
            audit_repo=audit_repo,
            action="VIEW_BUNDLE",
            resource_type="bundle",
            resource_id=bundle_id,
            status="FAILED",
            user=current_user,
            details={"error": "Not found", "version": version},
            tenant_id=tenant_id
        )
        raise HTTPException(status_code=404, detail=detail)
    
    log_audit(
        audit_repo=audit_repo,
        action="VIEW_BUNDLE",
        resource_type="bundle",
        resource_id=bundle_id,
        status="SUCCESS",
        user=current_user,
        details={"version": version},
        tenant_id=tenant_id
    )
    return SkillBundleResponse(
        bundle_id=record.bundle_id,
        bundle=SkillBundle(**record.payload),
        exported_at=record.created_at,
    )


@router.get("/bundles/{bundle_id}/versions", response_model=List[BundleRecord])
def get_bundle_versions(
    bundle_id: str,
    bundle_store: Optional[BundleStore] = Depends(get_bundle_store),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> List[BundleRecord]:
    """Retrieve all stored historical version records for a specific bundle ID."""
    if not bundle_store:
        raise HTTPException(status_code=400, detail="Persistence storage is disabled")
    
    versions = bundle_store.get_versions(bundle_id, tenant_id=tenant_id)
    log_audit(
        audit_repo=audit_repo,
        action="VIEW_BUNDLE_VERSIONS",
        resource_type="bundle",
        resource_id=bundle_id,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    return versions


@router.delete("/bundles/{bundle_id}")
def delete_bundle(
    bundle_id: str,
    bundle_store: Optional[BundleStore] = Depends(get_bundle_store),
    current_user: AuthenticatedUser = Depends(get_current_user),
    tenant_id: str = Depends(resolve_tenant_id),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> dict:
    """Delete all versions of a specific bundle from the store (Admin or owner only)."""
    if not bundle_store:
        raise HTTPException(status_code=400, detail="Persistence storage is disabled")
    
    record = bundle_store.get_bundle(bundle_id, tenant_id=tenant_id)
    if not record:
        log_audit(
            audit_repo=audit_repo,
            action="DELETE_BUNDLE",
            resource_type="bundle",
            resource_id=bundle_id,
            status="FAILED",
            user=current_user,
            details={"error": "Not found"},
            tenant_id=tenant_id
        )
        raise HTTPException(status_code=404, detail=f"Bundle '{bundle_id}' not found")

    # Enforce Scoped ownership check: Admin or resource creator owner
    check_tenant_bundle_ownership(current_user, tenant_id, record.created_by)

    success = bundle_store.delete_bundle(bundle_id, tenant_id=tenant_id)
    if not success:
        log_audit(
            audit_repo=audit_repo,
            action="DELETE_BUNDLE",
            resource_type="bundle",
            resource_id=bundle_id,
            status="FAILED",
            user=current_user,
            details={"error": "Deletion failed"},
            tenant_id=tenant_id
        )
        raise HTTPException(status_code=404, detail=f"Bundle '{bundle_id}' not found")
        
    log_audit(
        audit_repo=audit_repo,
        action="DELETE_BUNDLE",
        resource_type="bundle",
        resource_id=bundle_id,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    return {"deleted": True}


@router.get("/compilations/{compilation_id}", response_model=CompilationRecord)
def get_compilation(
    compilation_id: str,
    compilation_store: Optional[CompilationStore] = Depends(get_compilation_store),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_OPERATOR)),
    tenant_id: str = Depends(resolve_tenant_id),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> CompilationRecord:
    """Retrieve a specific compilation run history record."""
    if not compilation_store:
        raise HTTPException(status_code=400, detail="Persistence storage is disabled")
    
    record = compilation_store.get_compilation(compilation_id, tenant_id=tenant_id)
    if not record:
        log_audit(
            audit_repo=audit_repo,
            action="VIEW_COMPILATION",
            resource_type="compilation",
            resource_id=compilation_id,
            status="FAILED",
            user=current_user,
            details={"error": "Not found"},
            tenant_id=tenant_id
        )
        raise HTTPException(status_code=404, detail=f"Compilation run '{compilation_id}' not found")
        
    log_audit(
        audit_repo=audit_repo,
        action="VIEW_COMPILATION",
        resource_type="compilation",
        resource_id=compilation_id,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    return record


@router.get("/compilations/{compilation_id}/traces", response_model=List[CompilationTrace])
def get_compilation_traces(
    compilation_id: str,
    trace_store: Optional[TraceStore] = Depends(get_trace_store),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_OPERATOR)),
    tenant_id: str = Depends(resolve_tenant_id),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> List[CompilationTrace]:
    """Retrieve the intermediate step traces associated with a specific compilation run."""
    if not trace_store:
        raise HTTPException(status_code=400, detail="Persistence storage is disabled")
    
    traces = trace_store.get_traces_by_compilation(compilation_id, tenant_id=tenant_id)
    log_audit(
        audit_repo=audit_repo,
        action="VIEW_COMPILATION_TRACES",
        resource_type="compilation",
        resource_id=compilation_id,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    return traces


# --- Scoped Key Management Endpoints ---

@router.post("/auth/keys", response_model=CreateAPIKeyResponse)
def create_key(
    request: CreateAPIKeyRequest,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> CreateAPIKeyResponse:
    """Generate a new secure random API key and register its SHA-256 hash."""
    if not repo:
        raise HTTPException(status_code=500, detail="API key repository uninitialized.")

    manager = APIKeyManager(repo)
    raw_token, record = manager.create_api_key(
        name=request.name,
        role=request.role,
        tenant_id=tenant_id,
        tenant_role=request.tenant_role,
        global_role=request.global_role
    )
    
    log_audit(
        audit_repo=audit_repo,
        action="CREATE_KEY",
        resource_type="api_key",
        resource_id=record.key_id,
        status="SUCCESS",
        user=current_user,
        details={"name": request.name, "tenant_id": tenant_id},
        tenant_id=tenant_id
    )
    
    return CreateAPIKeyResponse(
        key_id=record.key_id,
        api_key=raw_token,
        role=record.role,
        tenant_id=record.tenant_id,
        tenant_role=record.tenant_role,
        global_role=record.global_role
    )


@router.get("/auth/keys", response_model=List[APIKeyInfo])
def list_keys(
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> List[APIKeyInfo]:
    """List metadata descriptions (name, prefix, creation date, status) of all registered keys."""
    if not repo:
        raise HTTPException(status_code=500, detail="API key repository uninitialized.")

    keys = repo.list_api_keys(tenant_id)
    
    log_audit(
        audit_repo=audit_repo,
        action="LIST_KEYS",
        resource_type="api_key",
        resource_id=None,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    
    return [
        APIKeyInfo(
            key_id=k.key_id,
            name=k.name,
            key_prefix=k.key_prefix,
            role=k.role,
            tenant_id=k.tenant_id,
            tenant_role=k.tenant_role,
            global_role=k.global_role,
            created_at=k.created_at,
            enabled=k.enabled
        ) for k in keys
    ]


@router.delete("/auth/keys/{key_id}")
def revoke_key(
    key_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> dict:
    """Revoke/disable an API key by setting its enabled state to false."""
    if not repo:
        raise HTTPException(status_code=500, detail="API key repository uninitialized.")

    key_record = repo.get_api_key_by_id(tenant_id, key_id)
    if not key_record:
        log_audit(
            audit_repo=audit_repo,
            action="REVOKE_KEY",
            resource_type="api_key",
            resource_id=key_id,
            status="FAILED",
            user=current_user,
            details={"error": "Not found"},
            tenant_id=tenant_id
        )
        raise HTTPException(status_code=404, detail=f"API Key '{key_id}' not found.")

    success = repo.revoke_api_key(tenant_id, key_id)
    if not success:
        log_audit(
            audit_repo=audit_repo,
            action="REVOKE_KEY",
            resource_type="api_key",
            resource_id=key_id,
            status="FAILED",
            user=current_user,
            details={"error": "Revocation failed"},
            tenant_id=tenant_id
        )
        raise HTTPException(status_code=404, detail=f"API Key '{key_id}' not found.")

    log_audit(
        audit_repo=audit_repo,
        action="REVOKE_KEY",
        resource_type="api_key",
        resource_id=key_id,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    return {"revoked": True}


@router.get("/audit/logs", response_model=List[AuditEventRecord])
def list_audit_logs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    audit_repo: Optional[SQLiteAuditRepository] = Depends(get_audit_repository),
) -> List[AuditEventRecord]:
    """Retrieve historical audit trace history records using pagination."""
    if not audit_repo:
        raise HTTPException(status_code=400, detail="Audit logger repository is disabled")

    logs = audit_repo.list_audit_events(tenant_id, limit, offset)
    log_audit(
        audit_repo=audit_repo,
        action="VIEW_AUDIT_LOGS",
        resource_type="audit",
        resource_id=None,
        status="SUCCESS",
        user=current_user,
        details={"limit": limit, "offset": offset},
        tenant_id=tenant_id
    )
    return logs


@router.post("/executions/start", response_model=JobStartResponse)
def start_execution(
    request: TriggerExecutionRequest,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_OPERATOR)),
    tenant_id: str = Depends(resolve_tenant_id),
    engine: ExecutionEngine = Depends(get_execution_engine),
    queue: JobQueue = Depends(get_job_queue),
) -> JobStartResponse:
    """Trigger execution of a compiled skill bundle asynchronously (requires TENANT_OPERATOR)."""
    if not engine:
        raise HTTPException(status_code=400, detail="Runtime engine is disabled")
    if not queue:
        raise HTTPException(status_code=400, detail="Job queue is disabled")

    v = request.version
    if v is None:
        bundle_store = get_bundle_store()
        if bundle_store:
            latest = bundle_store.get_bundle(request.bundle_id, tenant_id=tenant_id)
            if latest:
                v = latest.version
        if v is None:
            v = 1

    execution_id = f"exec_{uuid.uuid4().hex[:12]}"
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        # 1. Persist a PENDING execution record
        exec_record = ExecutionRecord(
            execution_id=execution_id,
            tenant_id=tenant_id,
            bundle_id=request.bundle_id,
            bundle_version=v,
            status=ExecutionStatus.PENDING,
            current_node_id=None,
            started_at=now_str,
            triggered_by=current_user.user_id,
            context_payload=request.input_data or {},
        )
        engine.repo.save_execution(tenant_id, exec_record)

        # 2. Create and Enqueue Job
        job = JobRecord(
            job_id=job_id,
            tenant_id=tenant_id,
            execution_id=execution_id,
            bundle_id=request.bundle_id,
            bundle_version=v,
            status=JobStatus.QUEUED,
            attempt_count=0,
            max_attempts=3,
            created_at=now_str,
            created_by=current_user.user_id,
            payload={"action": "execute", "initial_input": request.input_data or {}},
            priority=100,
        )
        queue.enqueue(job)

        # 3. Telemetry and Audits
        metrics_collector.record_job_created(tenant_id)
        log_audit(
            audit_repo=get_audit_repository(),
            action="JOB_CREATED",
            resource_type="job",
            resource_id=job_id,
            status="SUCCESS",
            user=current_user,
            details={"execution_id": execution_id},
            tenant_id=tenant_id,
        )

        return JobStartResponse(job_id=job_id, execution_id=execution_id, status="QUEUED")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/executions/{execution_id}/resume", response_model=ExecutionRecord)
def resume_execution(
    execution_id: str,
    request: ResumeExecutionRequest,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    engine: ExecutionEngine = Depends(get_execution_engine),
    queue: JobQueue = Depends(get_job_queue),
) -> ExecutionRecord:
    """Provide approval decision for a paused execution gate (requires TENANT_ADMIN)."""
    if not engine:
        raise HTTPException(status_code=400, detail="Runtime engine is disabled")
    if not queue:
        raise HTTPException(status_code=400, detail="Job queue is disabled")

    decision_str = request.decision.upper()
    if decision_str not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=400, detail="Decision must be 'APPROVED' or 'REJECTED'")

    try:
        # Find active job associated with execution_id
        import sqlite3
        job_id = None
        with queue.lock:
            conn = sqlite3.connect(queue.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT job_id FROM jobs WHERE tenant_id = ? AND execution_id = ? LIMIT 1",
                    (tenant_id, execution_id),
                )
                row = cursor.fetchone()
                if row:
                    job_id = row[0]
            finally:
                conn.close()

        if not job_id:
            raise HTTPException(status_code=404, detail=f"No background job found for execution '{execution_id}'")

        job = queue.get_job(tenant_id, job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

        # Re-enqueue the job for resume execution in background
        job.status = JobStatus.QUEUED
        job.worker_id = None
        job.run_at = None
        job.payload["action"] = "resume"
        job.payload["resume_data"] = {
            "decider_id": current_user.user_id,
            "decision": decision_str,
        }
        queue.update_job(tenant_id, job)

        # Retrieve current execution record
        exec_record = engine.repo.get_execution(tenant_id, execution_id)
        if not exec_record:
            raise HTTPException(status_code=404, detail="Execution not found")

        return exec_record
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/executions/{execution_id}/cancel", response_model=ExecutionRecord)
def cancel_execution(
    execution_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_OPERATOR)),
    tenant_id: str = Depends(resolve_tenant_id),
    engine: ExecutionEngine = Depends(get_execution_engine),
    queue: JobQueue = Depends(get_job_queue),
) -> ExecutionRecord:
    """Revoke/cancel a running or paused execution (requires TENANT_OPERATOR)."""
    if not engine:
        raise HTTPException(status_code=400, detail="Runtime engine is disabled")
    if not queue:
        raise HTTPException(status_code=400, detail="Job queue is disabled")

    try:
        record = engine.repo.get_execution(tenant_id, execution_id)
        if not record:
            raise HTTPException(status_code=404, detail="Execution not found")

        if current_user.global_role != GlobalRole.ADMIN:
            if current_user.tenant_role != TenantRole.TENANT_ADMIN:
                if record.triggered_by != current_user.user_id:
                    raise HTTPException(
                        status_code=403,
                        detail="Permission denied: Operators can only cancel their own executions"
                    )

        # Find associated job and cancel in queue (if QUEUED or RETRYING)
        import sqlite3
        job_id = None
        with queue.lock:
            conn = sqlite3.connect(queue.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT job_id FROM jobs WHERE tenant_id = ? AND execution_id = ? LIMIT 1",
                    (tenant_id, execution_id),
                )
                row = cursor.fetchone()
                if row:
                    job_id = row[0]
            finally:
                conn.close()

        if job_id:
            queue.cancel(tenant_id, job_id)

        return engine.cancel(
            execution_id=execution_id,
            tenant_id=tenant_id,
            cancelled_by=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/executions/{execution_id}", response_model=ExecutionRecord)
def get_execution(
    execution_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
) -> ExecutionRecord:
    """Retrieve execution run summary (requires TENANT_VIEWER)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    record = repo.get_execution(tenant_id, execution_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")
    return record


@router.get("/executions/{execution_id}/nodes", response_model=List[NodeExecutionRecord])
def get_execution_nodes(
    execution_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
) -> List[NodeExecutionRecord]:
    """Retrieve node-level step run details for an execution (requires TENANT_VIEWER)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    return repo.get_node_executions(tenant_id, execution_id)


@router.get("/executions/approvals/pending", response_model=List[ApprovalRecord])
def get_pending_approvals(
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
) -> List[ApprovalRecord]:
    """List pending human-in-the-loop approvals waiting for decision (requires TENANT_ADMIN)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    return repo.list_pending_approvals(tenant_id)


@router.get("/jobs", response_model=List[JobRecord])
def get_jobs(
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    queue: JobQueue = Depends(get_job_queue),
) -> List[JobRecord]:
    """List all jobs in the current tenant (requires TENANT_VIEWER)."""
    if not queue:
        raise HTTPException(status_code=500, detail="Job queue is not initialized")
    return queue.list_jobs(tenant_id)


@router.get("/jobs/{job_id}", response_model=JobRecord)
def get_job_by_id(
    job_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    queue: JobQueue = Depends(get_job_queue),
) -> JobRecord:
    """Retrieve job details by job_id (requires TENANT_VIEWER)."""
    if not queue:
        raise HTTPException(status_code=500, detail="Job queue is not initialized")
    job = queue.get_job(tenant_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job_by_id(
    job_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_OPERATOR)),
    tenant_id: str = Depends(resolve_tenant_id),
    queue: JobQueue = Depends(get_job_queue),
    engine: ExecutionEngine = Depends(get_execution_engine),
):
    """Cancel a queued or retrying job (requires TENANT_OPERATOR)."""
    if not queue:
        raise HTTPException(status_code=500, detail="Job queue is not initialized")
    
    job = queue.get_job(tenant_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    # Cancel in queue
    cancelled = queue.cancel(tenant_id, job_id)
    if not cancelled:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled (might not be in QUEUED or RETRYING state)")

    # Cancel associated execution
    if engine:
        try:
            engine.cancel(job.execution_id, tenant_id, current_user.user_id)
        except Exception:
            pass

    log_audit(
        audit_repo=get_audit_repository(),
        action="JOB_CANCELLED",
        resource_type="job",
        resource_id=job_id,
        status="SUCCESS",
        user=current_user,
        details={"execution_id": job.execution_id},
        tenant_id=tenant_id,
    )

    return {"cancelled": True}


@router.post("/schedules", response_model=ScheduleResponse)
def create_schedule(
    request: ScheduleCreateRequest,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_OPERATOR)),
    tenant_id: str = Depends(resolve_tenant_id),
    scheduler: CronScheduler = Depends(get_cron_scheduler),
) -> ScheduleResponse:
    """Create a new cron schedule for a skill bundle (requires TENANT_OPERATOR)."""
    if not scheduler:
        raise HTTPException(status_code=500, detail="Cron scheduler is not initialized")

    v = request.version
    if v is None:
        bundle_store = get_bundle_store()
        if bundle_store:
            latest = bundle_store.get_bundle(request.bundle_id, tenant_id=tenant_id)
            if latest:
                v = latest.version
        if v is None:
            v = 1

    from app.jobs.scheduler import get_next_run
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    now_str = now.isoformat().replace("+00:00", "Z")

    try:
        next_run_dt = get_next_run(request.cron_expression, now)
        next_run_str = next_run_dt.isoformat().replace("+00:00", "Z")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    schedule_id = f"sch_{uuid.uuid4().hex[:12]}"
    record = ScheduleRecord(
        schedule_id=schedule_id,
        tenant_id=tenant_id,
        bundle_id=request.bundle_id,
        bundle_version=v,
        cron_expression=request.cron_expression,
        enabled=True,
        next_run_at=next_run_str,
        created_by=current_user.user_id,
        created_at=now_str,
        last_triggered_at=None,
    )

    scheduler.save_schedule(record)

    log_audit(
        audit_repo=get_audit_repository(),
        action="SCHEDULE_CREATED",
        resource_type="schedule",
        resource_id=schedule_id,
        status="SUCCESS",
        user=current_user,
        details={"cron": request.cron_expression, "bundle_id": request.bundle_id},
        tenant_id=tenant_id,
    )

    return ScheduleResponse(
        schedule_id=record.schedule_id,
        tenant_id=record.tenant_id,
        bundle_id=record.bundle_id,
        bundle_version=record.bundle_version,
        cron_expression=record.cron_expression,
        enabled=record.enabled,
        next_run_at=record.next_run_at,
        created_by=record.created_by,
        created_at=record.created_at,
        last_triggered_at=record.last_triggered_at,
    )


@router.get("/schedules", response_model=List[ScheduleResponse])
def list_schedules(
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    scheduler: CronScheduler = Depends(get_cron_scheduler),
) -> List[ScheduleResponse]:
    """List all configured schedules in the tenant (requires TENANT_VIEWER)."""
    if not scheduler:
        raise HTTPException(status_code=500, detail="Cron scheduler is not initialized")
    
    schedules = scheduler.list_schedules(tenant_id)
    return [
        ScheduleResponse(
            schedule_id=s.schedule_id,
            tenant_id=s.tenant_id,
            bundle_id=s.bundle_id,
            bundle_version=s.bundle_version,
            cron_expression=s.cron_expression,
            enabled=s.enabled,
            next_run_at=s.next_run_at,
            created_by=s.created_by,
            created_at=s.created_at,
            last_triggered_at=s.last_triggered_at,
        )
        for s in schedules
    ]


@router.get("/schedules/{schedule_id}", response_model=ScheduleResponse)
def get_schedule_by_id(
    schedule_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    scheduler: CronScheduler = Depends(get_cron_scheduler),
) -> ScheduleResponse:
    """Retrieve schedule details by schedule_id (requires TENANT_VIEWER)."""
    if not scheduler:
        raise HTTPException(status_code=500, detail="Cron scheduler is not initialized")
    
    s = scheduler.get_schedule(tenant_id, schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")

    return ScheduleResponse(
        schedule_id=s.schedule_id,
        tenant_id=s.tenant_id,
        bundle_id=s.bundle_id,
        bundle_version=s.bundle_version,
        cron_expression=s.cron_expression,
        enabled=s.enabled,
        next_run_at=s.next_run_at,
        created_by=s.created_by,
        created_at=s.created_at,
        last_triggered_at=s.last_triggered_at,
    )


@router.delete("/schedules/{schedule_id}")
def delete_schedule_by_id(
    schedule_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_OPERATOR)),
    tenant_id: str = Depends(resolve_tenant_id),
    scheduler: CronScheduler = Depends(get_cron_scheduler),
):
    """Delete a schedule by schedule_id (requires TENANT_OPERATOR)."""
    if not scheduler:
        raise HTTPException(status_code=500, detail="Cron scheduler is not initialized")

    deleted = scheduler.delete_schedule(tenant_id, schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")

    log_audit(
        audit_repo=get_audit_repository(),
        action="SCHEDULE_DELETED",
        resource_type="schedule",
        resource_id=schedule_id,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id,
    )

    return {"deleted": True}


def redact_credentials(val: Any) -> Any:
    """Recursively search and redact keys matching credential patterns in dictionaries and lists."""
    if isinstance(val, dict):
        redacted = {}
        for k, v in val.items():
            k_lower = k.lower()
            if any(s in k_lower for s in ("password", "token", "api_key", "secret", "authorization", "credential", "private_key")):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_credentials(v)
        return redacted
    elif isinstance(val, list):
        return [redact_credentials(item) for item in val]
    else:
        return val


@router.get("/executions/{execution_id}/results")
def get_execution_results(
    execution_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
):
    """Retrieve execution results including context, query runs, and action runs (requires TENANT_VIEWER)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    
    record = repo.get_execution(tenant_id, execution_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")
        
    node_execs = repo.get_node_executions(tenant_id, execution_id)
    action_execs = repo.get_action_executions(tenant_id, execution_id)
    
    # Structure results payload
    results_payload = {
        "execution_id": record.execution_id,
        "status": record.status,
        "context_payload": record.context_payload,
        "query_executions": [node.model_dump() for node in node_execs],
        "action_executions": [action.model_dump() for action in action_execs],
        "generated_spl": record.context_payload.get("investigation_history", [{}])[-1].get("spl") if record.context_payload.get("investigation_history") else "",
        "results": record.context_payload.get("query_results", []),
        "investigation_history": record.context_payload.get("investigation_history", []),
        "executive_report": record.context_payload.get("executive_report", ""),
        "threat_classification": record.context_payload.get("threat_classification", {}),
        "evidence": record.context_payload.get("evidence", []),
    }
    
    # Recursively redact credentials from the final response dict
    return redact_credentials(results_payload)


@router.get("/executions/{execution_id}/investigation")
def get_execution_investigation(
    execution_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
):
    """Retrieve the multi-step investigation details of an execution (requires TENANT_VIEWER)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    
    record = repo.get_execution(tenant_id, execution_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")
        
    investigation_payload = {
        "execution_id": record.execution_id,
        "investigation_history": record.context_payload.get("investigation_history", []),
        "executive_report": record.context_payload.get("executive_report", ""),
        "query_results": record.context_payload.get("query_results", []),
        "query_events": record.context_payload.get("query_events", []),
        "query_stats": record.context_payload.get("query_stats", {}),
        "threat_classification": record.context_payload.get("threat_classification", {}),
        "evidence": record.context_payload.get("evidence", []),
    }
    return redact_credentials(investigation_payload)



# --- Secure Vault API Routes ---

from app.vault.models import SecretType, SecretRecord

class SecretCreateRequest(BaseModel):
    name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", description="Secret name matching ^[a-zA-Z0-9_-]+$")
    secret_type: SecretType = Field(..., description="Type of the secret")
    value: str = Field(..., min_length=1, description="Plaintext secret value")


class SecretRotateRequest(BaseModel):
    value: str = Field(..., min_length=1, description="New plaintext secret value")


class SecretMetadataResponse(BaseModel):
    secret_id: str
    tenant_id: str
    name: str
    secret_type: SecretType
    version: int
    enabled: bool
    is_current: bool
    created_at: str
    updated_at: str


@router.post("/vault/secrets", response_model=SecretMetadataResponse)
def create_secret(
    request: SecretCreateRequest,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo=Depends(get_audit_repository),
) -> SecretMetadataResponse:
    """Create a new secret in the tenant vault (requires TENANT_ADMIN)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    from app.vault.service import VaultService
    vault_service = VaultService(repo)
    try:
        record = vault_service.create_secret(
            tenant_id=tenant_id,
            name=request.name,
            secret_type=request.secret_type,
            plaintext=request.value
        )
        log_audit(
            audit_repo=audit_repo,
            action="SECRET_CREATED",
            resource_type="secret",
            resource_id=record.secret_id,
            status="SUCCESS",
            user=current_user,
            details={"name": request.name, "secret_type": request.secret_type.value},
            tenant_id=tenant_id
        )
        return SecretMetadataResponse(
            secret_id=record.secret_id,
            tenant_id=record.tenant_id,
            name=record.name,
            secret_type=record.secret_type,
            version=record.version,
            enabled=record.enabled,
            is_current=record.is_current,
            created_at=record.created_at,
            updated_at=record.updated_at
        )
    except ValueError as ve:
        log_audit(
            audit_repo=audit_repo,
            action="SECRET_CREATED",
            resource_type="secret",
            resource_id=None,
            status="FAILED",
            user=current_user,
            details={"error": str(ve), "name": request.name},
            tenant_id=tenant_id
        )
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vault/secrets", response_model=List[SecretMetadataResponse])
def list_secrets(
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
) -> List[SecretMetadataResponse]:
    """List all secret metadata under the tenant (requires TENANT_ADMIN)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    records = repo.list_secrets(tenant_id)
    return [
        SecretMetadataResponse(
            secret_id=r.secret_id,
            tenant_id=r.tenant_id,
            name=r.name,
            secret_type=r.secret_type,
            version=r.version,
            enabled=r.enabled,
            is_current=r.is_current,
            created_at=r.created_at,
            updated_at=r.updated_at
        )
        for r in records
    ]


@router.get("/vault/secrets/{secret_id}", response_model=SecretMetadataResponse)
def get_secret(
    secret_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
) -> SecretMetadataResponse:
    """Retrieve metadata for a specific secret by ID (requires TENANT_ADMIN)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    record = repo.get_secret(tenant_id, secret_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Secret '{secret_id}' not found")
    return SecretMetadataResponse(
        secret_id=record.secret_id,
        tenant_id=record.tenant_id,
        name=record.name,
        secret_type=record.secret_type,
        version=record.version,
        enabled=record.enabled,
        is_current=record.is_current,
        created_at=record.created_at,
        updated_at=record.updated_at
    )


@router.post("/vault/secrets/{secret_id}/rotate", response_model=SecretMetadataResponse)
def rotate_secret(
    secret_id: str,
    request: SecretRotateRequest,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo=Depends(get_audit_repository),
) -> SecretMetadataResponse:
    """Rotate the secret value, creating a new version (requires TENANT_ADMIN)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    from app.vault.service import VaultService
    vault_service = VaultService(repo)
    try:
        record = vault_service.rotate_secret(tenant_id, secret_id, request.value)
        log_audit(
            audit_repo=audit_repo,
            action="SECRET_ROTATED",
            resource_type="secret",
            resource_id=record.secret_id,
            status="SUCCESS",
            user=current_user,
            details={"name": record.name, "version": record.version},
            tenant_id=tenant_id
        )
        return SecretMetadataResponse(
            secret_id=record.secret_id,
            tenant_id=record.tenant_id,
            name=record.name,
            secret_type=record.secret_type,
            version=record.version,
            enabled=record.enabled,
            is_current=record.is_current,
            created_at=record.created_at,
            updated_at=record.updated_at
        )
    except ValueError as ve:
        log_audit(
            audit_repo=audit_repo,
            action="SECRET_ROTATED",
            resource_type="secret",
            resource_id=secret_id,
            status="FAILED",
            user=current_user,
            details={"error": str(ve)},
            tenant_id=tenant_id
        )
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/vault/secrets/{secret_id}")
def disable_secret(
    secret_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo=Depends(get_audit_repository),
):
    """Disable/Deactivate a secret (requires TENANT_ADMIN)."""
    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")
    from app.vault.service import VaultService
    vault_service = VaultService(repo)
    try:
        disabled = vault_service.disable_secret(tenant_id, secret_id)
        if not disabled:
            raise HTTPException(status_code=404, detail=f"Secret '{secret_id}' not found")
        log_audit(
            audit_repo=audit_repo,
            action="SECRET_DISABLED",
            resource_type="secret",
            resource_id=secret_id,
            status="SUCCESS",
            user=current_user,
            tenant_id=tenant_id
        )
        return {"disabled": True}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Connector Marketplace Endpoints ---

from app.connectors.models import ConnectorType

class ConnectorResponse(BaseModel):
    connector_id: str
    tenant_id: str
    connector_type: ConnectorType
    name: str
    description: Optional[str] = None
    enabled: bool
    configuration: Dict[str, Any]
    connector_version: int
    schema_version: int
    health_status: str
    last_health_check: Optional[str] = None
    last_success_at: Optional[str] = None
    consecutive_failures: int
    last_validation_at: Optional[str] = None
    validation_error: Optional[str] = None
    rate_limit_per_minute: int
    circuit_state: str
    circuit_failure_count: int
    circuit_opened_at: Optional[str] = None
    created_at: str
    updated_at: str


class ConnectorCreateRequest(BaseModel):
    connector_type: ConnectorType = Field(..., description="Operational connector type")
    name: str = Field(..., description="Friendly connector name")
    description: Optional[str] = Field(default=None, description="Detailed connector description")
    configuration: Dict[str, Any] = Field(default_factory=dict, description="Configuration parameters dictionary")
    rate_limit_per_minute: int = Field(default=100, description="Rate limit per minute")


class ConnectorUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    rate_limit_per_minute: Optional[int] = None
    enabled: Optional[bool] = None


@router.post("/connectors", response_model=ConnectorResponse)
def create_connector(
    request: ConnectorCreateRequest,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo=Depends(get_audit_repository),
) -> ConnectorResponse:
    """Create a new connector configuration and validate credentials."""
    from app.connectors.service import ConnectorService
    service = ConnectorService(repo)
    try:
        record = service.create_connector(
            tenant_id=tenant_id,
            connector_type=request.connector_type,
            name=request.name,
            configuration=request.configuration,
            description=request.description,
            rate_limit_per_minute=request.rate_limit_per_minute
        )
        log_audit(
            audit_repo=audit_repo,
            action="CONNECTOR_CREATED",
            resource_type="connector",
            resource_id=record.connector_id,
            status="SUCCESS",
            user=current_user,
            details={"name": record.name, "type": record.connector_type},
            tenant_id=tenant_id
        )
        # Redact config credentials
        redacted_record = record.copy(update={"configuration": redact_credentials(record.configuration)})
        return redacted_record
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/connectors", response_model=List[ConnectorResponse])
def list_connectors(
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
) -> List[ConnectorResponse]:
    """List all connectors inside the tenant."""
    from app.connectors.service import ConnectorService
    service = ConnectorService(repo)
    records = service.list_connectors(tenant_id)
    return [r.copy(update={"configuration": redact_credentials(r.configuration)}) for r in records]


@router.get("/connectors/{connector_id}", response_model=ConnectorResponse)
def get_connector(
    connector_id: str,
    version: Optional[int] = Query(None, description="Specific configuration version"),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
) -> ConnectorResponse:
    """Get metadata for a specific connector version or the latest version."""
    from app.connectors.service import ConnectorService
    service = ConnectorService(repo)
    record = service.get_connector(tenant_id, connector_id, version)
    if not record:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")
    return record.copy(update={"configuration": redact_credentials(record.configuration)})


@router.put("/connectors/{connector_id}", response_model=ConnectorResponse)
def update_connector(
    connector_id: str,
    request: ConnectorUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo=Depends(get_audit_repository),
) -> ConnectorResponse:
    """Update a connector configuration, generating a new version and validating credentials."""
    from app.connectors.service import ConnectorService
    service = ConnectorService(repo)
    try:
        record = service.update_connector(
            tenant_id=tenant_id,
            connector_id=connector_id,
            name=request.name,
            configuration=request.configuration,
            description=request.description,
            rate_limit_per_minute=request.rate_limit_per_minute,
            enabled=request.enabled
        )
        log_audit(
            audit_repo=audit_repo,
            action="CONNECTOR_UPDATED",
            resource_type="connector",
            resource_id=connector_id,
            status="SUCCESS",
            user=current_user,
            details={"name": record.name, "version": record.connector_version},
            tenant_id=tenant_id
        )
        return record.copy(update={"configuration": redact_credentials(record.configuration)})
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/connectors/{connector_id}")
def delete_connector(
    connector_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo=Depends(get_audit_repository),
):
    """Hard delete all versions of a connector configuration."""
    from app.connectors.service import ConnectorService
    service = ConnectorService(repo)
    deleted = service.delete_connector(tenant_id, connector_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")
    log_audit(
        audit_repo=audit_repo,
        action="CONNECTOR_DELETED",
        resource_type="connector",
        resource_id=connector_id,
        status="SUCCESS",
        user=current_user,
        tenant_id=tenant_id
    )
    return {"deleted": True}


@router.post("/connectors/{connector_id}/test")
def test_connector(
    connector_id: str,
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    audit_repo=Depends(get_audit_repository),
):
    """Run an on-demand sandbox connection test to validate connector credentials."""
    from app.connectors.service import ConnectorService
    service = ConnectorService(repo)
    try:
        res = service.test_connector(tenant_id, connector_id)
        status_str = "SUCCESS" if res["success"] else "FAILED"
        log_audit(
            audit_repo=audit_repo,
            action="CONNECTOR_TESTED",
            resource_type="connector",
            resource_id=connector_id,
            status=status_str,
            user=current_user,
            details={"success": res["success"], "error": res["error"]},
            tenant_id=tenant_id
        )
        return res
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Collaboration Approval Callback Endpoints ---

import json
from app.collaboration.models import ApprovalState

class DirectCallbackRequest(BaseModel):
    token: str = Field(..., description="Signed HMAC approval token")
    decision: str = Field(..., description="Decision choice: approve/reject")
    decided_by: str = Field(..., description="Identifier of the decider user")
    nonce: str = Field(..., description="Unique transaction identifier to prevent replay")
    timestamp: str = Field(..., description="Timestamp of the callback generation")


@router.post("/approvals/callback/slack")
async def slack_callback(
    request: Any,  # Typed as Any to bypass Pydantic model parsing for raw body
    repo=Depends(get_sqlite_repository),
    engine: ExecutionEngine = Depends(get_execution_engine),
):
    """Processes Slack interactive callbacks, validates signature/nonce, and resumes workflow execution."""
    from fastapi import Request
    if not isinstance(request, Request):
        raise HTTPException(status_code=500, detail="Invalid request context")
    
    headers = dict(request.headers)
    raw_body = await request.body()
    
    from urllib.parse import parse_qs
    try:
        parsed = parse_qs(raw_body.decode("utf-8"))
        payload_json = parsed.get("payload", ["{}"])[0]
        payload = json.loads(payload_json)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed Slack payload form data")

    timestamp_str = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    
    tenant_id = request.query_params.get("tenant_id") or payload.get("team", {}).get("id") or "system"

    from app.collaboration.callbacks import CallbackHandler
    handler = CallbackHandler(repo)
    try:
        res = handler.handle_slack_callback(
            tenant_id=tenant_id,
            timestamp_str=timestamp_str,
            signature=signature,
            raw_body=raw_body,
            payload=payload
        )
        
        # Resume execution
        approval_id = res["approval_id"]
        req_rec = repo.get_approval_request(tenant_id, approval_id)
        if req_rec:
            decision_enum = ApprovalStatus.APPROVED if res["status"] == ApprovalState.APPROVED else ApprovalStatus.REJECTED
            engine.resume(
                execution_id=req_rec.execution_id,
                decider_id=payload.get("user", {}).get("username") or "SlackUser",
                decision=decision_enum,
                tenant_id=tenant_id
            )
        return {"status": "ok", "decision": res["status"]}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approvals/callback/token")
def token_callback(
    request: DirectCallbackRequest,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    engine: ExecutionEngine = Depends(get_execution_engine),
):
    """Direct webhook callback using signed HMAC approval token."""
    from app.collaboration.callbacks import CallbackHandler
    handler = CallbackHandler(repo)
    try:
        res = handler.handle_token_callback(
            tenant_id=tenant_id,
            token=request.token,
            decision_str=request.decision,
            decided_by=request.decided_by,
            nonce=request.nonce,
            timestamp_str=request.timestamp
        )
        
        # Resume execution
        approval_id = res["approval_id"]
        req_rec = repo.get_approval_request(tenant_id, approval_id)
        if req_rec:
            decision_enum = ApprovalStatus.APPROVED if res["status"] == ApprovalState.APPROVED else ApprovalStatus.REJECTED
            engine.resume(
                execution_id=req_rec.execution_id,
                decider_id=request.decided_by,
                decision=decision_enum,
                tenant_id=tenant_id
            )
        return {"status": "ok", "decision": res["status"]}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Phase 22 Schemas ---

class CreatePolicyRequest(BaseModel):
    name: str = Field(..., description="Name of the governance policy")
    policy_type: str = Field(..., description="The scope category of the policy (EXECUTION, DEPLOYMENT, CONNECTOR, SECRET)")
    priority: int = Field(default=100, description="Conflict resolution priority")
    policy_definition: List[dict] = Field(..., description="List of rules")


class UpdatePolicyRequest(BaseModel):
    name: str
    enabled: bool
    priority: int
    policy_definition: List[dict]


class PolicyRollbackRequest(BaseModel):
    target_version: int


class PolicySimulateRequest(BaseModel):
    context: dict
    policy_definition: List[dict]


class PromoteRequest(BaseModel):
    bundle_id: str
    target_environment: str
    approver: Optional[str] = None
    comments: Optional[str] = None


class ValidatePromotionRequest(BaseModel):
    bundle_id: str
    target_environment: str


class RollbackBundleRequest(BaseModel):
    bundle_id: str
    target_version: int
    actor: str


class SystemFlagRequest(BaseModel):
    flag_name: str
    flag_value: str


# --- Phase 22 Endpoints ---

@router.post("/policies")
def create_policy(
    request: CreatePolicyRequest,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
):
    from app.governance.models import PolicyRecord, PolicyType
    policy_id = str(uuid.uuid4())
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = PolicyRecord(
        policy_id=policy_id,
        tenant_id=tenant_id,
        name=request.name,
        policy_type=PolicyType(request.policy_type),
        enabled=True,
        priority=request.priority,
        version=1,
        is_current=True,
        policy_definition=request.policy_definition,
        created_at=now_str,
        updated_at=now_str
    )
    repo.save_policy(tenant_id, record)
    return record


@router.get("/policies")
def list_policies(
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
):
    return repo.list_policies(tenant_id)


@router.get("/policies/{policy_id}")
def get_policy(
    policy_id: str,
    version: Optional[int] = Query(None, description="Get specific version"),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
):
    p = repo.get_policy(tenant_id, policy_id, version)
    if not p:
        raise HTTPException(status_code=404, detail="Policy not found")
    return p


@router.put("/policies/{policy_id}")
def update_policy(
    policy_id: str,
    request: UpdatePolicyRequest,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
):
    latest = repo.get_policy(tenant_id, policy_id)
    if not latest:
        raise HTTPException(status_code=404, detail="Policy not found")
    
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    from app.governance.models import PolicyRecord
    new_version = latest.version + 1
    record = PolicyRecord(
        policy_id=policy_id,
        tenant_id=tenant_id,
        name=request.name,
        policy_type=latest.policy_type,
        enabled=request.enabled,
        priority=request.priority,
        version=new_version,
        is_current=True,
        policy_definition=request.policy_definition,
        created_at=latest.created_at,
        updated_at=now_str
    )
    repo.save_policy(tenant_id, record)
    return record


@router.delete("/policies/{policy_id}")
def delete_policy(
    policy_id: str,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
):
    deleted = repo.delete_policy(tenant_id, policy_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Policy not found")
    return {"status": "deleted"}


@router.post("/policies/{policy_id}/rollback")
def rollback_policy(
    policy_id: str,
    request: PolicyRollbackRequest,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
):
    from app.governance.policy_engine import PolicyEngine
    engine = PolicyEngine(repo)
    try:
         rolled = engine.rollback_policy(tenant_id, policy_id, request.target_version)
         return rolled
    except ValueError as ve:
         raise HTTPException(status_code=400, detail=str(ve))


@router.post("/policies/simulate")
def simulate_policy(
    request: PolicySimulateRequest,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
):
    from app.governance.policy_engine import PolicyEngine
    engine = PolicyEngine(repo)
    res = engine.simulate(tenant_id, request.context, request.policy_definition)
    return res


@router.post("/deployments/validate")
def validate_promotion(
    request: ValidatePromotionRequest,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
):
    from app.governance.policy_engine import PolicyEngine
    from app.deployment.service import DeploymentService
    engine = PolicyEngine(repo)
    svc = DeploymentService(repo, engine)
    res = svc.validate_promotion(tenant_id, request.bundle_id, request.target_environment)
    return res


@router.post("/deployments/promote")
def promote_bundle(
    request: PromoteRequest,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
):
    from app.governance.policy_engine import PolicyEngine
    from app.deployment.service import DeploymentService
    engine = PolicyEngine(repo)
    svc = DeploymentService(repo, engine)
    try:
        res = svc.promote_bundle(
            tenant_id=tenant_id,
            bundle_id=request.bundle_id,
            target_environment=request.target_environment,
            approver=request.approver,
            comments=request.comments
        )
        return res
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/deployments/rollback")
def rollback_bundle(
    request: RollbackBundleRequest,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
):
    from app.governance.policy_engine import PolicyEngine
    from app.deployment.service import DeploymentService
    engine = PolicyEngine(repo)
    svc = DeploymentService(repo, engine)
    try:
        res = svc.rollback_bundle(
            tenant_id=tenant_id,
            bundle_id=request.bundle_id,
            target_version=request.target_version,
            actor=request.actor
        )
        return res
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/compliance/reports")
def generate_compliance_report_endpoint(
    report_type: str = Query("FULL"),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_ADMIN)),
):
    from app.compliance.reports import generate_compliance_report
    snapshot = generate_compliance_report(tenant_id, repo, report_type)
    return snapshot


@router.get("/compliance/reports")
def list_compliance_reports(
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
):
    return repo.list_compliance_snapshots(tenant_id)


@router.get("/compliance/reports/{snapshot_id}")
def get_compliance_report(
    snapshot_id: str,
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
):
    snapshot = repo.get_compliance_snapshot(tenant_id, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Compliance snapshot not found")
    return snapshot


@router.get("/compliance/reports/{snapshot_id}/download")
def download_compliance_report(
    snapshot_id: str,
    format: str = Query("json", description="Export format: json or csv"),
    tenant_id: str = Depends(resolve_tenant_id),
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_tenant_role(TenantRole.TENANT_VIEWER)),
):
    snapshot = repo.get_compliance_snapshot(tenant_id, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Compliance snapshot not found")
    
    if format.lower() == "csv":
        from app.compliance.reports import export_report_to_csv
        csv_data = export_report_to_csv(snapshot.report_data)
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=compliance_report_{snapshot_id}.csv"}
        )
    
    return snapshot.report_data


@router.post("/system/flags")
def set_system_flag(
    request: SystemFlagRequest,
    repo=Depends(get_sqlite_repository),
    current_user: AuthenticatedUser = Depends(require_global_admin),
):
    repo.save_system_flag(request.flag_name, request.flag_value, current_user.user_id)
    return {"status": "ok", "flag_name": request.flag_name, "flag_value": request.flag_value}




