import os
import tempfile
import pytest
from app.storage.sqlite import SQLiteRepository
from app.storage.models import BundleRecord, CompilationRecord, TraceRecord
from app.auth.models import APIKeyRecord, TenantRecord


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


def test_repository_tenant_isolation(temp_db_file):
    repo = SQLiteRepository(temp_db_file)

    # 1. Setup two tenants in database
    repo.save_tenant(TenantRecord(tenant_id="tenant-1", name="T1", slug="t1", created_at="2026-06-12T00:00:00Z", enabled=True))
    repo.save_tenant(TenantRecord(tenant_id="tenant-2", name="T2", slug="t2", created_at="2026-06-12T00:00:00Z", enabled=True))

    # 2. Persist bundle records scoped to each tenant
    b1 = BundleRecord(
        bundle_id="b-1",
        bundle_name="SOC Compile Runbook",
        version=1,
        created_at="2026-06-12T00:00:00Z",
        status="SUCCESS",
        payload={"step": 1},
        tenant_id="tenant-1",
        created_by="op-1"
    )
    b2 = BundleRecord(
        bundle_id="b-2",
        bundle_name="Finance Compliance Playbook",
        version=1,
        created_at="2026-06-12T00:00:00Z",
        status="SUCCESS",
        payload={"step": 2},
        tenant_id="tenant-2",
        created_by="op-2"
    )
    repo.save_bundle("tenant-1", b1)
    repo.save_bundle("tenant-2", b2)

    # 3. Assert strict isolation
    t1_bundles = repo.list_bundles("tenant-1")
    assert len(t1_bundles) == 1
    assert t1_bundles[0].bundle_id == "b-1"
    assert t1_bundles[0].bundle_name == "SOC Compile Runbook"

    t2_bundles = repo.list_bundles("tenant-2")
    assert len(t2_bundles) == 1
    assert t2_bundles[0].bundle_id == "b-2"
    assert t2_bundles[0].bundle_name == "Finance Compliance Playbook"

    # Verify same isolation for compilations
    comp1 = CompilationRecord(
        compilation_id="c-1",
        bundle_id="b-1",
        timestamp="2026-06-12T00:00:00Z",
        duration_ms=100.0,
        confidence=0.9,
        status="SUCCESS",
        tenant_id="tenant-1"
    )
    comp2 = CompilationRecord(
        compilation_id="c-2",
        bundle_id="b-2",
        timestamp="2026-06-12T00:00:00Z",
        duration_ms=120.0,
        confidence=0.85,
        status="SUCCESS",
        tenant_id="tenant-2"
    )
    repo.save_compilation("tenant-1", comp1)
    repo.save_compilation("tenant-2", comp2)

    assert len(repo.list_compilations("tenant-1", "b-1")) == 1
    assert len(repo.list_compilations("tenant-2", "b-1")) == 0
