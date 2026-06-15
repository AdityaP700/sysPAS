import logging
from typing import Dict, Any, Optional

from app.storage.sqlite import SQLiteRepository
from app.connectors.service import ConnectorService
from app.connectors.models import ConnectorType
from app.collaboration.models import NotificationTemplateRecord

logger = logging.getLogger(__name__)


class Notifier:
    """Notifier Service responsible for rendering and dispatching notifications with fallback routing."""

    def __init__(self, repo: SQLiteRepository):
        self.repo = repo
        self.connector_service = ConnectorService(repo)

    def format_template(self, template_str: Optional[str], context: Dict[str, Any]) -> str:
        """Safely formats templates replacing placeholders with context values."""
        if not template_str:
            return ""
        result = template_str
        for k, v in context.items():
            placeholder = "{" + str(k) + "}"
            if placeholder in result:
                result = result.replace(placeholder, str(v))
        return result

    def send_notification(
        self,
        tenant_id: str,
        event_type: str,
        channel: str,
        context: Dict[str, Any],
        fallback_channel: Optional[str] = None
    ) -> bool:
        """
        Formats and sends a notification using the tenant's templates.
        If the primary dispatch fails or has no configured connector, attempts the fallback_channel.
        """
        success = self._dispatch(tenant_id, event_type, channel, context)
        if not success and fallback_channel:
            logger.warning(
                f"Primary channel '{channel}' failed for event '{event_type}' in tenant '{tenant_id}'. "
                f"Attempting fallback channel '{fallback_channel}'."
            )
            return self._dispatch(tenant_id, event_type, fallback_channel, context)
        return success

    def _dispatch(self, tenant_id: str, event_type: str, channel: str, context: Dict[str, Any]) -> bool:
        # 1. Fetch template
        template = self.repo.get_notification_template(tenant_id, event_type, channel)
        
        subject = ""
        body = ""
        if template:
            subject = self.format_template(template.subject_template, context)
            body = self.format_template(template.body_template, context)
        else:
            # Fallback to default rendering if no template exists
            subject = f"Alert: {event_type}"
            body = f"Notification event '{event_type}' triggered. Context: {str(context)}"

        # 2. Find target connector
        try:
            connectors = self.repo.list_connectors(tenant_id)
        except Exception as e:
            logger.error(f"Failed to list connectors: {str(e)}")
            return False

        target_type = None
        if channel.upper() == "SLACK":
            target_type = ConnectorType.SLACK
        elif channel.upper() in ("MICROSOFT_TEAMS", "TEAMS"):
            target_type = ConnectorType.MICROSOFT_TEAMS
        elif channel.upper() == "JIRA":
            target_type = ConnectorType.JIRA
        elif channel.upper() == "SERVICENOW":
            target_type = ConnectorType.SERVICENOW
        elif channel.upper() == "PAGERDUTY":
            target_type = ConnectorType.PAGERDUTY

        if not target_type:
            logger.warning(f"Unsupported notification channel mapping: {channel}")
            return False

        connector_record = None
        for conn in connectors:
            if conn.connector_type == target_type and conn.enabled:
                connector_record = conn
                break

        if not connector_record:
            logger.warning(f"No enabled connector found for channel '{channel}' in tenant '{tenant_id}'")
            return False

        # 3. Execute payload
        try:
            instance = self.connector_service._get_connector_instance(tenant_id, connector_record)
            if target_type == ConnectorType.SLACK:
                payload = {
                    "text": f"{subject}\n\n{body}"
                }
                # Support passing Slack interactive blocks if defined in context
                if "blocks" in context:
                    payload["blocks"] = context["blocks"]
                if "slack_channel" in context:
                    payload["channel"] = context["slack_channel"]
            elif target_type == ConnectorType.MICROSOFT_TEAMS:
                payload = {
                    "title": subject,
                    "text": body
                }
                if "teams_card" in context:
                    payload["card"] = context["teams_card"]
            else:
                payload = {
                    "title": subject,
                    "summary": subject,
                    "description": body,
                    **context
                }

            instance.execute(payload)
            return True
        except Exception as e:
            logger.error(f"Notification dispatch failed on channel '{channel}': {str(e)}")
            return False
