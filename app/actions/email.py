import time
import uuid
from app.actions.base import BaseActionConnector
from app.actions.models import ActionResult


class EmailConnector(BaseActionConnector):
    """Action connector responsible for routing email notifications (initially mocked)."""

    def validate(self, payload: dict) -> None:
        """Enforce requirements for recipient, subject, and message body."""
        if not payload.get("to"):
            raise ValueError("Email validation failed: Recipient 'to' is required and cannot be empty")
        if not payload.get("subject"):
            raise ValueError("Email validation failed: 'subject' is required")
        if not payload.get("body"):
            raise ValueError("Email validation failed: 'body' is required")

    def execute(self, payload: dict) -> ActionResult:
        """Simulates sending email synchronously and returns message ID details."""
        start_time = time.perf_counter()
        self.validate(payload)
        
        # Simulating SMTP routing delay
        time.sleep(0.01)
        
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        
        return ActionResult(
            success=True,
            action_type="SEND_EMAIL",
            external_id=msg_id,
            details={
                "to": payload["to"],
                "subject": payload["subject"],
                "status": "SENT",
                "info": "Notification email routed successfully via mock SMTP gateway."
            },
            duration_ms=duration_ms
        )
