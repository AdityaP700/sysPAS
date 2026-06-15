import time
import uuid
from fastapi import FastAPI, Request
from app.web.routes import router
from app.observability.request_context import set_request_id, set_correlation_id, request_id_var, correlation_id_var
from app.observability.logging import logger
from app.observability.metrics import metrics_collector
from app.auth.middleware import AuthenticationMiddleware

app = FastAPI(
    title="RunbookMind REST API",
    description="Exposes the RunbookMind compiler pipeline to parse, compile, and export security playbooks.",
    version="1.0.0"
)


# Telemetry middleware handles requests tracking and request IDs context binding
@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    """FastAPI Middleware to trace request timings, handle IDs contextually, and update API counters."""
    start_time = time.perf_counter()

    # Track API request metric (using context resolved tenant_id)
    from app.observability.request_context import get_tenant_id
    tenant_id = get_tenant_id() or "system"
    metrics_collector.record_api_request(tenant_id)

    # Middleware is the sole creator of request_id
    req_id = str(uuid.uuid4())
    
    # Resolve correlation_id from header if present, else generate it
    corr_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())

    # Bind IDs to context variables
    token_req = set_request_id(req_id)
    token_corr = set_correlation_id(corr_id)

    status = "success"
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        
        # Increment error count if status indicates failure
        if status_code >= 400:
            status = "failed"
            metrics_collector.record_api_error(tenant_id)
            
        # Propagate context back in headers
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Correlation-ID"] = corr_id
        return response
    except Exception as e:
        status = "error"
        metrics_collector.record_api_error(tenant_id)
        raise e
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        
        # Log request completion in structured JSON
        logger.info(
            f"HTTP Request: {request.method} {request.url.path} - {status_code}",
            extra={
                "component": "api",
                "operation": f"{request.method} {request.url.path}",
                "duration_ms": round(duration_ms, 2),
                "status": status
            }
        )
        
        # Clear/reset context variables after request completion
        request_id_var.reset(token_req)
        correlation_id_var.reset(token_corr)


# Add Authentication Middleware
app.add_middleware(AuthenticationMiddleware)

# Register routes
app.include_router(router)


@app.on_event("startup")
def bootstrap_security():
    """
    Startup hook ensuring at least one administrator API key is configured.
    If auth is enabled and no keys are active, verifies default_admin_api_key exists
    in configuration settings, registers it, or fails application startup.
    Also validates Vault Master Key length and settings if Vault is enabled.
    """
    from app.config.settings import settings
    if settings.vault_enabled:
        if not settings.vault_master_key or len(settings.vault_master_key) < 32:
            key_len = len(settings.vault_master_key) if settings.vault_master_key else 0
            raise RuntimeError(
                f"Critical Security Configuration Failure: 'vault_master_key' must be at least 32 characters when vault is enabled (current: {key_len})."
            )

    if settings.auth_enabled:
        from app.web.dependencies import get_sqlite_repository
        repo = get_sqlite_repository()
        if repo:
            from app.auth.models import GlobalRole, TenantRole
            active_admins = [
                key for key in repo.list_api_keys()
                if key.global_role == GlobalRole.ADMIN and key.enabled
            ]
            if not active_admins:
                if not settings.default_admin_api_key:
                    raise RuntimeError(
                        "Critical Security Configuration Failure: 'default_admin_api_key' "
                        "must be provided when auth_enabled is True and no active admin key exists in the database."
                    )
                # Register the default bootstrap admin key
                from datetime import datetime, timezone
                from app.auth.api_keys import APIKeyManager
                from app.auth.models import APIKeyRecord

                manager = APIKeyManager(repo)
                raw_key = settings.default_admin_api_key.strip()
                key_hash = manager.compute_hash(raw_key)
                prefix = manager.extract_prefix(raw_key)

                bootstrap_rec = APIKeyRecord(
                    key_id="bootstrap_admin",
                    name="Default Bootstrap Administrator",
                    key_hash=key_hash,
                    key_prefix=prefix,
                    global_role=GlobalRole.ADMIN,
                    tenant_role=TenantRole.TENANT_ADMIN,
                    tenant_id="system",
                    created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    enabled=True
                )
                repo.save_api_key(bootstrap_rec)


@app.on_event("startup")
def start_background_jobs():
    """Start background worker and scheduler threads on application startup."""
    from app.web.dependencies import get_background_worker, get_cron_scheduler
    worker = get_background_worker()
    if worker:
        worker.start()
    scheduler = get_cron_scheduler()
    if scheduler:
        scheduler.start()


@app.on_event("shutdown")
def stop_background_jobs():
    """Stop background worker and scheduler threads gracefully on application shutdown."""
    from app.web.dependencies import get_background_worker, get_cron_scheduler
    worker = get_background_worker()
    if worker:
        worker.stop()
    scheduler = get_cron_scheduler()
    if scheduler:
        scheduler.stop()
