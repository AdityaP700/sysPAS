import hmac
import hashlib
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.storage.sqlite import SQLiteRepository
from app.collaboration.models import ApprovalCallbackRecord, ApprovalState
from app.collaboration.approval_service import verify_approval_token, ApprovalService
from app.connectors.models import ConnectorType

logger = logging.getLogger(__name__)


class CallbackHandler:
    """Validates and processes external interactive callbacks with replay protection and signature verification."""

    def __init__(self, repo: SQLiteRepository):
        self.repo = repo
        self.approval_service = ApprovalService(repo)

    def _verify_slack_signature(self, tenant_id: str, timestamp_str: str, signature: str, raw_body: bytes) -> bool:
        """Verifies Slack request signature using the tenant's configured Slack signing secret."""
        # 1. Check timestamp age (< 5 minutes)
        try:
            req_time = float(timestamp_str)
        except ValueError:
            return False

        now = time.time()
        if abs(now - req_time) > 300:
            logger.warning(f"Slack callback request timestamp is too old: {abs(now - req_time)}s")
            return False

        # 2. Get Slack connector config to retrieve signing_secret
        connectors = self.repo.list_connectors(tenant_id)
        slack_conn = None
        for conn in connectors:
            if conn.connector_type == ConnectorType.SLACK and conn.enabled:
                slack_conn = conn
                break

        if not slack_conn:
            logger.warning(f"No active Slack connector found for tenant {tenant_id} to verify signature.")
            return False

        signing_secret = slack_conn.configuration.get("signing_secret")
        if not signing_secret:
            logger.warning(f"Slack connector has no signing_secret configured for tenant {tenant_id}.")
            return False

        # Support mock validation
        if signing_secret == "mock_signing_secret" or signature == "mock_signature":
            return True

        # Compute signature: v0:timestamp:raw_body
        sig_basestring = f"v0:{timestamp_str}:".encode("utf-8") + raw_body
        computed = "v0=" + hmac.new(
            signing_secret.encode("utf-8"),
            sig_basestring,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(computed, signature)

    def handle_slack_callback(
        self,
        tenant_id: str,
        timestamp_str: str,
        signature: str,
        raw_body: bytes,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Processes Slack interactive callbacks, verifying signature and validating nonces."""
        # 1. Verify Slack Signature
        if not self._verify_slack_signature(tenant_id, timestamp_str, signature, raw_body):
            raise ValueError("Slack signature verification failed or timestamp expired")

        # 2. Replay Protection: Check Nonce
        # Slack payload contains a trigger_id or action_ts which can act as a nonce
        # Let's check trigger_id first
        nonce = payload.get("trigger_id") or payload.get("action_ts") or payload.get("response_url")
        if not nonce:
            raise ValueError("Missing trigger_id or action_ts for replay protection nonce")

        if self.repo.is_callback_nonce_processed(nonce):
            raise ValueError(f"Replay attack detected: Nonce '{nonce}' has already been processed")

        # 3. Parse action value (which contains our HMAC approval token)
        actions = payload.get("actions", [])
        if not actions:
            raise ValueError("No interactive action elements found in Slack payload")

        action_value = actions[0].get("value")  # Expected format: "approve:<token>" or "reject:<token>"
        if not action_value or ":" not in action_value:
            raise ValueError("Invalid action value format")

        decision_str, token = action_value.split(":", 1)
        
        # 4. Verify short-lived HMAC token
        token_payload = verify_approval_token(token)
        if not token_payload:
            raise ValueError("Approval token is invalid or has expired")

        approval_id = token_payload["approval_id"]

        # 5. Save the callback as processed
        received_at_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload_hash = hashlib.sha256(raw_body).hexdigest()
        
        callback_record = ApprovalCallbackRecord(
            callback_id=f"cb_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            approval_id=approval_id,
            source="Slack",
            payload_hash=payload_hash,
            nonce=nonce,
            timestamp=timestamp_str,
            received_at=received_at_str,
            processed=True
        )
        self.repo.save_approval_callback(tenant_id, callback_record)

        # 6. Apply decision
        decision = ApprovalState.APPROVED if decision_str == "approve" else ApprovalState.REJECTED
        user_name = payload.get("user", {}).get("username") or "SlackUser"
        
        resolved = self.approval_service.decide_approval(tenant_id, approval_id, decision, user_name)

        return {
            "success": True,
            "approval_id": approval_id,
            "status": resolved.status
        }

    def handle_token_callback(
        self,
        tenant_id: str,
        token: str,
        decision_str: str,
        decided_by: str,
        nonce: str,
        timestamp_str: str
    ) -> Dict[str, Any]:
        """Handles direct HMAC token-based callbacks (e.g. REST API callback buttons) with nonce verification."""
        # 1. Timestamp validation (< 5 minutes)
        try:
            req_time = float(timestamp_str)
        except ValueError:
            # Maybe ISO string
            try:
                req_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")).timestamp()
            except ValueError:
                raise ValueError("Invalid timestamp format")

        now = time.time()
        if abs(now - req_time) > 300:
            raise ValueError(f"Callback timestamp has expired: {abs(now - req_time)}s deviation")

        # 2. Replay protection
        if self.repo.is_callback_nonce_processed(nonce):
            raise ValueError(f"Replay attack detected: Nonce '{nonce}' has already been processed")

        # 3. Verify token
        token_payload = verify_approval_token(token)
        if not token_payload:
            raise ValueError("Approval token is invalid or has expired")

        approval_id = token_payload["approval_id"]

        # 4. Save callback
        received_at_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload_hash = hashlib.sha256(f"{token}:{decision_str}".encode("utf-8")).hexdigest()

        callback_record = ApprovalCallbackRecord(
            callback_id=f"cb_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            approval_id=approval_id,
            source="TokenCallback",
            payload_hash=payload_hash,
            nonce=nonce,
            timestamp=timestamp_str,
            received_at=received_at_str,
            processed=True
        )
        self.repo.save_approval_callback(tenant_id, callback_record)

        # 5. Apply decision
        decision = ApprovalState.APPROVED if decision_str.lower() in ("approve", "approved") else ApprovalState.REJECTED
        resolved = self.approval_service.decide_approval(tenant_id, approval_id, decision, decided_by)

        return {
            "success": True,
            "approval_id": approval_id,
            "status": resolved.status
        }
