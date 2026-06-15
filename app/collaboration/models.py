from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalRequestRecord(BaseModel):
    approval_id: str
    tenant_id: str
    execution_id: str
    node_id: str
    status: ApprovalState = ApprovalState.PENDING
    escalation_level: int = 1
    escalated_to: Optional[str] = None
    escalation_policy: Dict[str, Any] = Field(default_factory=dict)
    approval_token: Optional[str] = None
    created_at: str
    decided_at: Optional[str] = None
    decision: Optional[ApprovalState] = None
    decided_by: Optional[str] = None
    expires_at: str


class ApprovalCallbackRecord(BaseModel):
    callback_id: str
    tenant_id: str
    approval_id: str
    source: str
    payload_hash: Optional[str] = None
    nonce: Optional[str] = None
    timestamp: str
    received_at: str
    processed: bool = False


class ApprovalReminderRecord(BaseModel):
    reminder_id: str
    approval_id: str
    reminder_number: int
    sent_at: str


class IncidentLinkRecord(BaseModel):
    link_id: str
    tenant_id: str
    execution_id: str
    connector_id: str
    external_system: str
    external_id: str
    status: str
    created_at: str


class NotificationTemplateRecord(BaseModel):
    template_id: str
    tenant_id: str
    event_type: str
    channel: str
    subject_template: Optional[str] = None
    body_template: Optional[str] = None
    created_at: str
    updated_at: str
