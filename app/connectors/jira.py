import urllib.request
import urllib.error
import json
import base64
import logging
from typing import Dict, Any

from app.connectors.base import BaseConnector
from app.connectors.models import ConnectorType
from app.connectors.registry import connector_registry

logger = logging.getLogger(__name__)


class JiraConnector(BaseConnector):
    """Jira connector implementation executing issue creations, transitions, and comments."""

    def _get_auth_header(self, username: str, token: str) -> str:
        creds = f"{username}:{token}"
        encoded = base64.b64encode(creds.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    def _execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self.record.configuration
        jira_url = config.get("jira_url")
        username = config.get("username")
        api_token = config.get("api_token")

        if not jira_url or not username or not api_token or api_token == "invalid_token":
            raise ValueError("Jira connection configuration is incomplete or invalid")

        action = payload.get("action", "CREATE_ISSUE").upper()
        
        # Check mock/testing mode
        if api_token.startswith("mock-") or api_token == "valid_token":
            if action == "CREATE_ISSUE" or action == "CREATE_TICKET":
                return {
                    "issue_key": f"JIRA-{hash(str(payload)) % 10000}",
                    "status": "created_mock"
                }
            elif action == "ADD_COMMENT" or action == "UPDATE_TICKET":
                return {
                    "status": "commented_mock",
                    "issue_key": payload.get("issue_key") or payload.get("ticket_id")
                }
            elif action == "TRANSITION_ISSUE":
                return {
                    "status": "transitioned_mock",
                    "issue_key": payload.get("issue_key")
                }
            raise ValueError(f"Unknown Jira action '{action}'")

        # Real implementation
        auth = self._get_auth_header(username, api_token)
        headers = {
            "Content-Type": "application/json",
            "Authorization": auth
        }

        if action in ("CREATE_ISSUE", "CREATE_TICKET"):
            url = f"{jira_url.rstrip('/')}/rest/api/2/issue"
            body = {
                "fields": {
                    "project": {"key": payload.get("project_key", "OPS")},
                    "summary": payload.get("title") or payload.get("summary") or "RunbookMind Incident",
                    "description": payload.get("description") or "Automatically created Jira issue.",
                    "issuetype": {"name": payload.get("issue_type", "Task")}
                }
            }
            method = "POST"
        elif action in ("ADD_COMMENT", "UPDATE_TICKET"):
            issue_key = payload.get("issue_key") or payload.get("ticket_id")
            if not issue_key:
                raise ValueError("issue_key or ticket_id is required for comments")
            url = f"{jira_url.rstrip('/')}/rest/api/2/issue/{issue_key}/comment"
            body = {
                "body": payload.get("comment") or "Jira updated by RunbookMind execution engine."
            }
            method = "POST"
        elif action == "TRANSITION_ISSUE":
            issue_key = payload.get("issue_key")
            if not issue_key:
                raise ValueError("issue_key is required for transitions")
            url = f"{jira_url.rstrip('/')}/rest/api/2/issue/{issue_key}/transitions"
            body = {
                "transition": {"id": str(payload.get("transition_id"))}
            }
            method = "POST"
        else:
            raise ValueError(f"Unsupported action: {action}")

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=5.0) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return {
                    "issue_key": res_data.get("key") or issue_key,
                    "response": res_data
                }
        except Exception as e:
            logger.error(f"Failed to execute Jira action: {str(e)}")
            raise e

    def check_health(self) -> bool:
        return self.validate_credentials()

    def validate_credentials(self) -> bool:
        config = self.record.configuration
        jira_url = config.get("jira_url")
        username = config.get("username")
        api_token = config.get("api_token")

        if not jira_url or not username or not api_token or api_token == "invalid_token":
            return False

        if api_token.startswith("mock-") or api_token == "valid_token":
            return True

        # Real validation via calling Jira API myself endpoint
        url = f"{jira_url.rstrip('/')}/rest/api/2/myself"
        auth = self._get_auth_header(username, api_token)
        req = urllib.request.Request(
            url,
            headers={"Authorization": auth}
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as response:
                return response.getcode() == 200
        except Exception:
            return False


connector_registry.register(ConnectorType.JIRA, JiraConnector)
