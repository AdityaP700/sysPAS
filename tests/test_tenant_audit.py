import os
import tempfile
import pytest
from app.audit.models import AuditEventRecord
from app.audit.repository import SQLiteAuditRepository
from app.observability.metrics import metrics_collector


@pytest.fixture
def temp_db_file():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_tenant_context_in_audit_logs(temp_db_file):
    repo = SQLiteAuditRepository(temp_db_file)
    
    # 1. Create two logs in different tenants
    a1 = AuditEventRecord(
        audit_id="a-1",
        timestamp="2026-06-12T00:00:00Z",
        request_id="req-1",
        correlation_id="corr-1",
        user_id="user-1",
        role="ADMIN",
        action="VIEW_BUNDLES",
        resource_type="bundle",
        resource_id="bundle-1",
        status="SUCCESS",
        details={},
        tenant_id="tenant-1"
    )
    a2 = AuditEventRecord(
        audit_id="a-2",
        timestamp="2026-06-12T00:00:00Z",
        request_id="req-2",
        correlation_id="corr-2",
        user_id="user-2",
        role="ADMIN",
        action="VIEW_BUNDLES",
        resource_type="bundle",
        resource_id="bundle-2",
        status="SUCCESS",
        details={},
        tenant_id="tenant-2"
    )
    repo.save_audit_event("tenant-1", a1)
    repo.save_audit_event("tenant-2", a2)

    # 2. Check tenant-specific listing
    t1_logs = repo.list_audit_events("tenant-1")
    assert len(t1_logs) == 1
    assert t1_logs[0].audit_id == "a-1"

    t2_logs = repo.list_audit_events("tenant-2")
    assert len(t2_logs) == 1
    assert t2_logs[0].audit_id == "a-2"


def test_tenant_aware_metrics_aggregation():
    metrics_collector.reset()
    
    # Record API requests for tenant-1 and tenant-2
    metrics_collector.record_api_request("tenant-1")
    metrics_collector.record_api_request("tenant-1")
    metrics_collector.record_api_request("tenant-2")

    assert metrics_collector.tenant_api_requests["tenant-1"] == 2
    assert metrics_collector.tenant_api_requests["tenant-2"] == 1

    # Record compilations
    metrics_collector.record_compilation(success=True, duration_ms=100.0, tenant_id="tenant-1")
    metrics_collector.record_compilation(success=False, duration_ms=200.0, tenant_id="tenant-1")
    metrics_collector.record_compilation(success=True, duration_ms=150.0, tenant_id="tenant-2")

    assert metrics_collector.tenant_compilations["tenant-1"] == 2
    assert metrics_collector.tenant_compilation_success["tenant-1"] == 1
    assert metrics_collector.tenant_compilation_failure["tenant-1"] == 1
    
    assert metrics_collector.average_duration_ms_for_tenant("tenant-1") == 150.0
    assert metrics_collector.average_duration_ms_for_tenant("tenant-2") == 150.0
