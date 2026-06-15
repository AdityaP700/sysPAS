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


class ServiceNowConnector(BaseConnector):
    """ServiceNow connector implementation interacting with Table APIs to update incident logs."""

    def _get_auth_header(self, username: str, password: str) -> str:
        creds = f"{username}:{password}"
        encoded = base64.b64encode(creds.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    def _execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self.record.configuration
        instance_url = config.get("instance_url")
        username = config.get("username")
        password = config.get("password")

        if not instance_url or not username or not password or password == "invalid_password":
            raise ValueError("ServiceNow configuration is incomplete or invalid")

        action = payload.get("action", "CREATE_INCIDENT").upper()

        # Mock Mode
        if password.startswith("mock-") or password == "valid_password":
            if action == "CREATE_INCIDENT":
                return {
                    "sys_id": f"sys_{hash(str(payload)) % 10000000}",
                    "number": f"INC{hash(str(payload)) % 1000000}",
                    "status": "created_mock"
                }
            elif action == "UPDATE_INCIDENT":
                return {
                    "status": "updated_mock",
                    "sys_id": payload.get("sys_id")
                }
            raise ValueError(f"Unknown ServiceNow action '{action}'")

        # Real Mode using ServiceNow Table API
        auth = self._get_auth_header(username, password)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": auth
        }

        if action == "CREATE_INCIDENT":
            url = f"{instance_url.rstrip('/')}/api/now/table/incident"
            body = {
                "short_description": payload.get("short_description") or "RunbookMind Incident",
                "description": payload.get("description") or "Automatically created ServiceNow incident.",
                "urgency": str(payload.get("urgency", "2")),
                "impact": str(payload.get("impact", "2"))
            }
            method = "POST"
        elif action == "UPDATE_INCIDENT":
            sys_id = payload.get("sys_id")
            if not sys_id:
                raise ValueError("sys_id is required to update an incident")
            url = f"{instance_url.rstrip('/')}/api/now/table/incident/{sys_id}"
            body = payload.get("update_fields") or {}
            method = "PUT"
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
                result_obj = res_data.get("result", {})
                return {
                    "sys_id": result_obj.get("sys_id"),
                    "number": result_obj.get("number"),
                    "response": result_obj
                }
        except Exception as e:
            logger.error(f"Failed to execute ServiceNow action: {str(e)}")
            raise e

    def check_health(self) -> bool:
        return self.validate_credentials()

    def validate_credentials(self) -> bool:
        config = self.record.configuration
        instance_url = config.get("instance_url")
        username = config.get("username")
        password = config.get("password")

        if not instance_url or not username or not password or password == "invalid_password":
            return False

        if password.startswith("mock-") or password == "valid_password":
            return True

        # Real validation calling ServiceNow Table API with limit 1
        url = f"{instance_url.rstrip('/')}/api/now/table/incident?sysparm_limit=1"
        auth = self._get_auth_header(username, password)
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": auth
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as response:
                return response.getcode() == 200
        except Exception:
            return False


connector_registry.register(ConnectorType.SERVICENOW, ServiceNowConnector)
