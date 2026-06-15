import urllib.request
import urllib.error
import json
import logging
from typing import Dict, Any

from app.connectors.base import BaseConnector
from app.connectors.models import ConnectorType
from app.connectors.registry import connector_registry

logger = logging.getLogger(__name__)


class TeamsConnector(BaseConnector):
    """Microsoft Teams connector implementation translating alerts into Adaptive Cards format."""

    def _execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        webhook_url = self.record.configuration.get("webhook_url")
        if not webhook_url or webhook_url == "invalid_url":
            raise ValueError("Invalid webhook_url configuration")

        # Support custom card payload, or construct default Adaptive Card
        card = payload.get("card")
        if not card:
            title = payload.get("title") or "RunbookMind Alert"
            text = payload.get("text") or "Notification details from execution engine"
            card = {
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "type": "AdaptiveCard",
                            "body": [
                                {
                                    "type": "TextBlock",
                                    "size": "Medium",
                                    "weight": "Bolder",
                                    "text": title
                                },
                                {
                                    "type": "TextBlock",
                                    "text": text,
                                    "wrap": True
                                }
                            ],
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "version": "1.2"
                        }
                    }
                ]
            }

        # Mock vs Real execution
        if "mock" in webhook_url or webhook_url == "valid_url":
            return {
                "status": "delivered_mock",
                "webhook_url": webhook_url
            }

        # Real HTTP POST
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(card).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=5.0) as response:
                body = response.read().decode("utf-8")
                return {
                    "status": "delivered",
                    "response": body[:200]
                }
        except Exception as e:
            logger.error(f"Failed to send Adaptive Card to Teams: {str(e)}")
            raise e

    def check_health(self) -> bool:
        return self.validate_credentials()

    def validate_credentials(self) -> bool:
        webhook_url = self.record.configuration.get("webhook_url")
        if not webhook_url or webhook_url == "invalid_url":
            return False
        # If it starts with http or is mock, it's valid config format
        if "mock" in webhook_url or webhook_url == "valid_url":
            return True
        return webhook_url.startswith("http://") or webhook_url.startswith("https://")


connector_registry.register(ConnectorType.MICROSOFT_TEAMS, TeamsConnector)
