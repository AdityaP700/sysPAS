import pytest
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta

from app.storage.sqlite import SQLiteRepository
from app.collaboration.models import ApprovalState, ApprovalRequestRecord
from app.collaboration.approval_service import (
    ApprovalService,
    generate_approval_token,
    verify_approval_token,
)
from app.collaboration.callbacks import CallbackHandler
from app.config.settings import settings
from app.connectors.models import ConnectorType, ConnectorRecord


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = SQLiteRepository(path)
    # Save a tenant
    from app.auth.models import TenantRecord
    repo.save_tenant(TenantRecord(tenant_id="tenant-coll", name="Tenant Coll", slug="tenant-coll", created_at="2026-06-13T00:00:00Z"))
    yield repo
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_hmac_token_generation_and_verification():
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    try:
        approval_id = "appr_123"
        tenant_id = "tenant-coll"
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=100)).isoformat().replace("+00:00", "Z")

        # 1. Generate Token
        token = generate_approval_token(approval_id, tenant_id, expires_at)
        assert token is not None
        assert "." in token

        # 2. Verify Valid Token
        payload = verify_approval_token(token)
        assert payload is not None
        assert payload["approval_id"] == approval_id
        assert payload["tenant_id"] == tenant_id

        # 3. Fail Verification on Forged Signature
        forged = token[:-4] + "xxxx"
        assert verify_approval_token(forged) is None

        # 4. Fail Verification on Expired Token
        expired_time = (now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        expired_token = generate_approval_token(approval_id, tenant_id, expired_time)
        assert verify_approval_token(expired_token) is None
    finally:
        settings.vault_master_key = old_key


def test_approval_creation_decide_and_escalation(temp_db):
    service = ApprovalService(temp_db)
    tenant_id = "tenant-coll"

    # Define policy with short timeouts
    policy = {
        "levels": [
            {"level": 1, "approver": "ops@runbookmind.local", "timeout_seconds": 1},
            {"level": 2, "approver": "manager@runbookmind.local", "timeout_seconds": 2}
        ]
    }

    # 1. Create Approval
    req = service.create_approval_request(
        tenant_id=tenant_id,
        execution_id="exec-123",
        node_id="node-approv",
        escalation_policy=policy,
        expires_in_seconds=100
    )

    assert req.status == ApprovalState.PENDING
    assert req.escalation_level == 1
    assert req.escalated_to == "ops@runbookmind.local"

    # 2. Test Escalation Trigger
    time.sleep(1.1)  # Sleep past level 1 timeout
    service.check_and_process_escalations()

    # Should be escalated to level 2
    updated = temp_db.get_approval_request(tenant_id, req.approval_id)
    assert updated.escalation_level == 2
    assert updated.escalated_to == "manager@runbookmind.local"

    # 3. Decide Approval
    resolved = service.decide_approval(tenant_id, req.approval_id, ApprovalState.APPROVED, "ManagerUser")
    assert resolved.status == ApprovalState.APPROVED
    assert resolved.decided_by == "ManagerUser"
    assert resolved.decision == ApprovalState.APPROVED


def test_callback_replay_protection_and_token_processing(temp_db):
    handler = CallbackHandler(temp_db)
    tenant_id = "tenant-coll"

    # Create a pending approval
    policy = {"levels": [{"level": 1, "approver": "ops@runbookmind.local", "timeout_seconds": 10}]}
    req = handler.approval_service.create_approval_request(
        tenant_id=tenant_id,
        execution_id="exec-123",
        node_id="node-approv",
        escalation_policy=policy,
        expires_in_seconds=200
    )

    nonce = "uniq_nonce_123"
    now_ts = str(time.time())

    # 1. Process valid callback first time
    res = handler.handle_token_callback(
        tenant_id=tenant_id,
        token=req.approval_token,
        decision_str="approved",
        decided_by="OperatorA",
        nonce=nonce,
        timestamp_str=now_ts
    )
    assert res["success"] is True
    assert res["status"] == ApprovalState.APPROVED

    # 2. Replay same callback -> raises Replay protection ValueError
    with pytest.raises(ValueError) as exc:
        handler.handle_token_callback(
            tenant_id=tenant_id,
            token=req.approval_token,
            decision_str="approved",
            decided_by="OperatorA",
            nonce=nonce,
            timestamp_str=now_ts
        )
    assert "replay attack" in str(exc.value).lower()


def test_callback_timestamp_check(temp_db):
    handler = CallbackHandler(temp_db)
    tenant_id = "tenant-coll"

    # Old timestamp (e.g. 6 minutes ago)
    old_ts = str(time.time() - 360)

    # Throws ValueError on expired callback timestamp
    with pytest.raises(ValueError) as exc:
        handler.handle_token_callback(
            tenant_id=tenant_id,
            token="dummy_token",
            decision_str="approved",
            decided_by="OperatorA",
            nonce="nonce_expiry_test",
            timestamp_str=old_ts
        )
    assert "expired" in str(exc.value).lower()
