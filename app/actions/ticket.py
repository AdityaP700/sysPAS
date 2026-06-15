import time
import uuid
from app.actions.base import BaseActionConnector
from app.actions.models import ActionResult


class TicketConnector(BaseActionConnector):
    """Action connector responsible for creating and updating ticketing system incidents (initially mocked)."""

    def validate(self, payload: dict) -> None:
        """Enforces validation rules for ticket operations depending on sub-action type."""
        action = payload.get("action", "CREATE_TICKET")
        
        if action == "CREATE_TICKET":
            if not payload.get("title"):
                raise ValueError("Ticket validation failed: 'title' is required for ticket creation")
            if not payload.get("description"):
                raise ValueError("Ticket validation failed: 'description' is required for ticket creation")
        elif action == "UPDATE_TICKET":
            if not payload.get("ticket_id"):
                raise ValueError("Ticket validation failed: 'ticket_id' is required for ticket update")
            if not payload.get("title") and not payload.get("description") and not payload.get("comment"):
                raise ValueError("Ticket validation failed: Must provide at least one field ('title', 'description', or 'comment') to update")
        else:
            raise ValueError(f"Ticket validation failed: Unsupported sub-action '{action}'")

    def execute(self, payload: dict) -> ActionResult:
        """Simulates creating/updating JIRA or ServiceNow incidents synchronously."""
        start_time = time.perf_counter()
        self.validate(payload)
        
        action = payload.get("action", "CREATE_TICKET")
        
        # Simulating external REST API delay
        time.sleep(0.01)
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        
        if action == "CREATE_TICKET":
            ticket_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
            return ActionResult(
                success=True,
                action_type="CREATE_TICKET",
                external_id=ticket_id,
                details={
                    "title": payload["title"],
                    "priority": payload.get("priority", "High"),
                    "status": "OPEN",
                    "info": f"Mock ticket '{ticket_id}' successfully created."
                },
                duration_ms=duration_ms
            )
        else:
            ticket_id = payload["ticket_id"]
            return ActionResult(
                success=True,
                action_type="UPDATE_TICKET",
                external_id=ticket_id,
                details={
                    "ticket_id": ticket_id,
                    "updated_fields": [k for k in ("title", "description", "comment") if k in payload],
                    "status": "UPDATED",
                    "info": f"Mock ticket '{ticket_id}' successfully updated."
                },
                duration_ms=duration_ms
            )
