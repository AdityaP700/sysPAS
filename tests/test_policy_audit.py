import pytest
import os
import tempfile
from app.storage.sqlite import SQLiteRepository
from app.audit.repository import SQLiteAuditRepository
from app.governance.models import PolicyRecord, PolicyType
from app.governance.policy_engine import PolicyEngine
from app.deployment.service import DeploymentService
from app.storage.models import BundleRecord


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = SQLiteRepository(path)
    audit_repo = SQLiteAuditRepository(path)
    
    yield repo, audit_repo
    
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_audit_logs_for_governance_actions(temp_db):
    repo, audit_repo = temp_db
    engine = PolicyEngine(repo)
    svc = DeploymentService(repo, engine)

    # 1. Create a bundle
    bundle = BundleRecord(
        bundle_id="b-audit-1",
        bundle_name="Audit SOP",
        version=1,
        created_at="2026-06-13T12:00:00Z",
        status="COMPILED",
        payload={"steps": []},
        tenant_id="tenant-1",
        created_by="ops",
        environment="DEV",
        promotion_status="DRAFT"
    )
    repo.save_bundle("tenant-1", bundle)

    # 2. Promote it
    svc.promote_bundle("tenant-1", "b-audit-1", "STAGING", approver="ops-admin", comments="Promoting for audit verification")

    # Verify BUNDLE_PROMOTE is in audit logs
    audits = audit_repo.list_audit_events("tenant-1")
    actions = [a.action for a in audits]
    assert "BUNDLE_PROMOTE" in actions

    # 3. Rollback the bundle
    svc.rollback_bundle("tenant-1", "b-audit-1", 1, actor="ops-admin")

    # Verify BUNDLE_ROLLBACK is in audit logs
    audits = audit_repo.list_audit_events("tenant-1")
    actions = [a.action for a in audits]
    assert "BUNDLE_ROLLBACK" in actions

    # 4. Rollback policy and verify audit log
    p_rec = PolicyRecord(
        policy_id="p-aud-1",
        tenant_id="tenant-1",
        name="Audit Policy",
        policy_type=PolicyType.EXECUTION,
        enabled=True,
        priority=100,
        version=1,
        is_current=True,
        policy_definition=[],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )
    repo.save_policy("tenant-1", p_rec)

    # Create version 2 of policy
    p_rec_v2 = PolicyRecord(
        policy_id="p-aud-1",
        tenant_id="tenant-1",
        name="Audit Policy",
        policy_type=PolicyType.EXECUTION,
        enabled=True,
        priority=100,
        version=2,
        is_current=True,
        policy_definition=[],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )
    repo.save_policy("tenant-1", p_rec_v2)

    # Roll back policy
    engine.rollback_policy("tenant-1", "p-aud-1", 1)

    # Verify POLICY_ROLLBACK is in audit logs
    audits = audit_repo.list_audit_events("tenant-1")
    actions = [a.action for a in audits]
    assert "POLICY_ROLLBACK" in actions
