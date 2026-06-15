import pytest
import os
import tempfile
from app.storage.sqlite import SQLiteRepository
from app.storage.models import BundleRecord
from app.governance.models import PolicyRecord, PolicyType
from app.governance.policy_engine import PolicyEngine
from app.deployment.service import DeploymentService


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


def test_bundle_environment_promotion_pipeline(temp_db):
    repo = temp_db
    engine = PolicyEngine(repo)
    svc = DeploymentService(repo, engine)

    # 1. Create a bundle in DEV environment
    bundle = BundleRecord(
        bundle_id="b1",
        bundle_name="Auth Check Runbook",
        version=1,
        created_at="2026-06-13T12:00:00Z",
        status="COMPILED",
        payload={"steps": [{"action": "check_auth"}]},
        tenant_id="tenant-1",
        created_by="dev-1",
        environment="DEV",
        promotion_status="DRAFT"
    )
    repo.save_bundle("tenant-1", bundle)

    # 2. Promote to STAGING (no approval required by default deployment policy)
    promoted = svc.promote_bundle("tenant-1", "b1", "STAGING")
    assert promoted.version == 2
    assert promoted.environment == "STAGING"
    assert promoted.promotion_status == "PROMOTED"

    # 3. Try promoting to PRODUCTION without an approver (should fail/require approval)
    with pytest.raises(ValueError) as exc:
        svc.promote_bundle("tenant-1", "b1", "PRODUCTION")
    assert "requires explicit approval" in str(exc.value)

    # Verify a pending deployment and approval were recorded
    deployments = repo.list_deployments("tenant-1")
    assert len(deployments) == 2
    assert deployments[0].environment == "PRODUCTION"
    assert deployments[0].status == "PENDING"

    approvals = repo.get_deployment_approvals("tenant-1", deployments[0].deployment_id)
    assert len(approvals) == 1
    assert approvals[0].decision == "PENDING"

    # 4. Now promote to PRODUCTION with an approver (should succeed)
    promoted_prod = svc.promote_bundle("tenant-1", "b1", "PRODUCTION", approver="ops-admin", comments="Approved by Ops admin")
    assert promoted_prod.version == 3
    assert promoted_prod.environment == "PRODUCTION"
    assert promoted_prod.promotion_status == "APPROVED"

    # Verify snapshot was created before promotion
    snapshot = repo.get_deployment_snapshot_by_deployment("tenant-1", deployments[0].deployment_id)
    # Wait, the second promote created a different deployment ID. Let's find the latest deployment
    deps = repo.list_deployments("tenant-1")
    success_dep = [d for d in deps if d.status == "SUCCESS"][0]
    snapshot_prod = repo.get_deployment_snapshot_by_deployment("tenant-1", success_dep.deployment_id)
    assert snapshot_prod is not None
    assert snapshot_prod.bundle_payload == {"steps": [{"action": "check_auth"}]}


def test_environment_isolation_violations(temp_db):
    repo = temp_db
    engine = PolicyEngine(repo)
    svc = DeploymentService(repo, engine)

    # 1. Add deployment policy: DEV secrets/configurations cannot be promoted to STAGING or PRODUCTION
    p_isolation = PolicyRecord(
        policy_id="p-iso-1",
        tenant_id="tenant-1",
        name="Environment isolation",
        policy_type=PolicyType.DEPLOYMENT,
        enabled=True,
        priority=100,
        version=1,
        is_current=True,
        policy_definition=[
            {
                "if": {"environment": "PRODUCTION", "action": "PROMOTION"},
                # Assume if target environment is PRODUCTION, restrict some characteristics
                # E.g. let's block promotion if context has a flag
                "then": {"allowed": False, "message": "Blocked by isolation boundary"}
            }
        ],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )
    repo.save_policy("tenant-1", p_isolation)

    # 2. Create bundle
    bundle = BundleRecord(
        bundle_id="b1",
        bundle_name="Auth Check Runbook",
        version=1,
        created_at="2026-06-13T12:00:00Z",
        status="COMPILED",
        payload={"steps": []},
        tenant_id="tenant-1",
        created_by="dev-1",
        environment="DEV",
        promotion_status="DRAFT"
    )
    repo.save_bundle("tenant-1", bundle)

    # 3. Promote to PRODUCTION should be blocked by policy
    with pytest.raises(ValueError) as exc:
        svc.promote_bundle("tenant-1", "b1", "PRODUCTION", approver="ops-admin")
    assert "Blocked by isolation boundary" in str(exc.value)


def test_bundle_rollback_integrity(temp_db):
    repo = temp_db
    engine = PolicyEngine(repo)
    svc = DeploymentService(repo, engine)

    # 1. Save bundle version 1
    bundle_v1 = BundleRecord(
        bundle_id="b-roll-1",
        bundle_name="Rollback SOP",
        version=1,
        created_at="2026-06-13T12:00:00Z",
        status="COMPILED",
        payload={"steps": [{"step": 1}]},
        tenant_id="tenant-1",
        created_by="dev-1",
        environment="DEV",
        promotion_status="DRAFT"
    )
    repo.save_bundle("tenant-1", bundle_v1)

    # 2. Save bundle version 2 (simulate update)
    bundle_v2 = BundleRecord(
        bundle_id="b-roll-1",
        bundle_name="Rollback SOP",
        version=2,
        created_at="2026-06-13T12:10:00Z",
        status="COMPILED",
        payload={"steps": [{"step": 1}, {"step": 2}]},
        tenant_id="tenant-1",
        created_by="dev-1",
        environment="DEV",
        promotion_status="DRAFT"
    )
    repo.save_bundle("tenant-1", bundle_v2)

    # 3. Rollback to version 1
    rolled = svc.rollback_bundle("tenant-1", "b-roll-1", 1, actor="ops-engineer")
    assert rolled.version == 3
    assert rolled.payload == {"steps": [{"step": 1}]}
    assert rolled.promotion_status == "DRAFT"
