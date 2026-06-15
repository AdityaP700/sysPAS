import hmac
import hashlib
import base64
import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from app.storage.sqlite import SQLiteRepository
from app.config.settings import settings
from app.collaboration.models import (
    ApprovalRequestRecord,
    ApprovalState,
    ApprovalReminderRecord,
)
from app.collaboration.notifier import Notifier

logger = logging.getLogger(__name__)


def generate_approval_token(approval_id: str, tenant_id: str, expires_at_str: str) -> str:
    """Generates a secure short-lived HMAC signature token representing the approval action."""
    key = (settings.vault_master_key or "default-hmac-key-for-approvals-key").encode("utf-8")
    payload = {
        "approval_id": approval_id,
        "tenant_id": tenant_id,
        "expires_at": expires_at_str
    }
    payload_json = json.dumps(payload)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("utf-8")
    signature = hmac.new(key, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_approval_token(token: str) -> Optional[Dict[str, Any]]:
    """Verifies the token signature and checks if the expiration date has passed."""
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_b64, signature = parts
        key = (settings.vault_master_key or "default-hmac-key-for-approvals-key").encode("utf-8")
        expected_sig = hmac.new(key, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
        payload = json.loads(payload_json)
        
        # Check expiration
        expires_at_str = payload.get("expires_at")
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_at:
            return None
        
        return payload
    except Exception:
        return None


class ApprovalService:
    """Manages approval creation, decision updates, signature tokens, escalation progression, and reminders."""

    def __init__(self, repo: SQLiteRepository):
        self.repo = repo
        self.notifier = Notifier(repo)

    def create_approval_request(
        self,
        tenant_id: str,
        execution_id: str,
        node_id: str,
        escalation_policy: Dict[str, Any],
        expires_in_seconds: int = 1800
    ) -> ApprovalRequestRecord:
        """Create an approval request and dispatch primary notification."""
        approval_id = f"appr_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_in_seconds)
        
        now_str = now.isoformat().replace("+00:00", "Z")
        expires_str = expires_at.isoformat().replace("+00:00", "Z")

        # Extract primary contact from the policy
        levels = escalation_policy.get("levels", [])
        primary_approver = None
        if levels:
            primary_approver = levels[0].get("approver")

        # Generate HMAC token
        token = generate_approval_token(approval_id, tenant_id, expires_str)

        record = ApprovalRequestRecord(
            approval_id=approval_id,
            tenant_id=tenant_id,
            execution_id=execution_id,
            node_id=node_id,
            status=ApprovalState.PENDING,
            escalation_level=1,
            escalated_to=primary_approver,
            escalation_policy=escalation_policy,
            approval_token=token,
            created_at=now_str,
            decided_at=None,
            decision=None,
            decided_by=None,
            expires_at=expires_str
        )

        self.repo.save_approval_request(tenant_id, record)

        # Notify primary contact
        self._send_approval_notification(record)

        return record

    def decide_approval(self, tenant_id: str, approval_id: str, decision: ApprovalState, decided_by: str) -> ApprovalRequestRecord:
        """Applies a decision (APPROVED/REJECTED) to a pending approval request."""
        record = self.repo.get_approval_request(tenant_id, approval_id)
        if not record:
            raise ValueError(f"Approval request '{approval_id}' not found in tenant '{tenant_id}'")

        if record.status != ApprovalState.PENDING:
            raise ValueError(f"Approval request '{approval_id}' is already resolved: status={record.status}")

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record.status = decision
        record.decision = decision
        record.decided_by = decided_by
        record.decided_at = now_str

        self.repo.save_approval_request(tenant_id, record)

        # Audit event could be added here
        return record

    def check_and_process_escalations(self) -> None:
        """Scheduled job method processing expirations, escalations, and reminder dispatching."""
        try:
            tenants = self.repo.list_tenants()
        except Exception as e:
            logger.error(f"Failed to list tenants for escalation checking: {str(e)}")
            return

        for tenant in tenants:
            tenant_id = tenant.tenant_id
            try:
                requests = self.repo.list_approval_requests(tenant_id)
            except Exception as e:
                logger.error(f"Failed to list approval requests for tenant {tenant_id}: {str(e)}")
                continue

            for record in requests:
                if record.status != ApprovalState.PENDING:
                    continue

                now = datetime.now(timezone.utc)
                now_str = now.isoformat().replace("+00:00", "Z")

                # 1. Check expiration
                expires_at = datetime.fromisoformat(record.expires_at.replace("Z", "+00:00"))
                if now > expires_at:
                    record.status = ApprovalState.EXPIRED
                    record.decided_at = now_str
                    self.repo.save_approval_request(tenant_id, record)
                    # Notify expiry
                    self.notifier.send_notification(
                        tenant_id=tenant_id,
                        event_type="APPROVAL_EXPIRED",
                        channel="SLACK",
                        context={
                            "approval_id": record.approval_id,
                            "execution_id": record.execution_id,
                            "node_id": record.node_id,
                            "approver": record.escalated_to
                        },
                        fallback_channel="MICROSOFT_TEAMS"
                    )
                    continue

                # 2. Check escalation timeout
                created_at = datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
                elapsed = (now - created_at).total_seconds()

                levels = record.escalation_policy.get("levels", [])
                current_level_idx = record.escalation_level - 1

                if current_level_idx < len(levels):
                    current_level_config = levels[current_level_idx]
                    timeout = current_level_config.get("timeout_seconds", 300)

                    # If elapsed time exceeds timeout, escalate to next level if available
                    if elapsed >= timeout and (current_level_idx + 1) < len(levels):
                        next_level_config = levels[current_level_idx + 1]
                        
                        record.escalation_level += 1
                        record.escalated_to = next_level_config.get("approver")
                        
                        self.repo.save_approval_request(tenant_id, record)
                        
                        # Notify new contact of escalation
                        self._send_approval_notification(record, is_escalation=True)
                        continue

                # 3. Check persistent reminders
                # Send a reminder if no reminder has been sent, or if time since last reminder exceeds a interval
                # Let's say we send a reminder every 60 seconds (or level timeout / 2)
                reminders = self.repo.get_approval_reminders(record.approval_id)
                reminder_interval = 60.0  # seconds

                time_since_last_sent = elapsed
                if reminders:
                    last_reminder = max(reminders, key=lambda r: r.reminder_number)
                    last_sent_at = datetime.fromisoformat(last_reminder.sent_at.replace("Z", "+00:00"))
                    time_since_last_sent = (now - last_sent_at).total_seconds()

                if time_since_last_sent >= reminder_interval:
                    reminder_number = len(reminders) + 1
                    
                    # Log reminder
                    reminder_record = ApprovalReminderRecord(
                        reminder_id=f"rem_{uuid.uuid4().hex[:12]}",
                        approval_id=record.approval_id,
                        reminder_number=reminder_number,
                        sent_at=now_str
                    )
                    self.repo.save_approval_reminder(reminder_record)

                    # Send reminder notification
                    self.notifier.send_notification(
                        tenant_id=tenant_id,
                        event_type="APPROVAL_REMINDER",
                        channel="SLACK",
                        context={
                            "approval_id": record.approval_id,
                            "execution_id": record.execution_id,
                            "node_id": record.node_id,
                            "approver": record.escalated_to,
                            "reminder_number": reminder_number,
                            "token": record.approval_token
                        },
                        fallback_channel="MICROSOFT_TEAMS"
                    )

    def _send_approval_notification(self, record: ApprovalRequestRecord, is_escalation: bool = False) -> None:
        """Helper to format and dispatch approval message with action buttons context."""
        event_type = "APPROVAL_ESCALATED" if is_escalation else "APPROVAL_REQUESTED"
        
        context = {
            "approval_id": record.approval_id,
            "execution_id": record.execution_id,
            "node_id": record.node_id,
            "approver": record.escalated_to,
            "token": record.approval_token,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Approval Required* (Execution: `{record.execution_id}`, Node: `{record.node_id}`)\n"
                                f"Approver: `{record.escalated_to}` (Level: {record.escalation_level})"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Approve"},
                            "style": "primary",
                            "value": f"approve:{record.approval_token}"
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Reject"},
                            "style": "danger",
                            "value": f"reject:{record.approval_token}"
                        }
                    ]
                }
            ]
        }

        self.notifier.send_notification(
            tenant_id=record.tenant_id,
            event_type=event_type,
            channel="SLACK",
            context=context,
            fallback_channel="MICROSOFT_TEAMS"
        )
