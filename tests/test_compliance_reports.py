import pytest
import os
import tempfile
import hashlib
import json
from app.storage.sqlite import SQLiteRepository
from app.governance.models import PolicyEventRecord, DeploymentRecord
from app.compliance.reports import generate_compliance_report, export_report_to_csv


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = SQLiteRepository(path)
    
    yield repo
    
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_compliance_report_lifecycle(temp_db):
    repo = temp_db

    # 1. Seed some policy events and deployment runs in DB
    event1 = PolicyEventRecord(
        event_id="e1",
        tenant_id="tenant-1",
        policy_id="p1",
        resource_type="CONNECTOR",
        resource_id="slack-1",
        decision="ALLOW",
        timestamp="2026-06-13T12:00:00Z",
        expires_at="2026-07-13T12:00:00Z"
    )
    repo.save_policy_event("tenant-1", event1)

    dep1 = DeploymentRecord(
        deployment_id="d1",
        tenant_id="tenant-1",
        bundle_id="b1",
        version=1,
        environment="PRODUCTION",
        status="SUCCESS",
        created_at="2026-06-13T12:05:00Z"
    )
    repo.save_deployment("tenant-1", dep1)

    # 2. Generate report
    snapshot = generate_compliance_report("tenant-1", repo)
    assert snapshot.report_type == "FULL"
    assert snapshot.tenant_id == "tenant-1"
    
    # 3. Verify cryptographic integrity checksum
    serialized = json.dumps(snapshot.report_data, sort_keys=True)
    expected_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    assert snapshot.snapshot_hash == expected_hash

    # Check that it is retrievable from the DB
    retrieved = repo.get_compliance_snapshot("tenant-1", snapshot.snapshot_id)
    assert retrieved is not None
    assert retrieved.snapshot_hash == expected_hash

    # 4. Verify CSV Export formatting
    csv_data = export_report_to_csv(snapshot.report_data)
    assert "COMPLIANCE REPORT SUMMARY" in csv_data
    assert "e1" in csv_data
    assert "d1" in csv_data
