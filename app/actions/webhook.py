import json
import socket
import time
import urllib.request
import urllib.error
import ipaddress
from urllib.parse import urlparse
from app.actions.base import BaseActionConnector
from app.actions.models import ActionResult
from app.config.settings import settings


class WebhookConnector(BaseActionConnector):
    """Action connector responsible for dispatching HTTP POST webhooks with SSRF and allowlist guards."""

    def _is_private_ip(self, ip_str: str) -> bool:
        """Helper checking if target IP resides inside private/loopback address blocks."""
        try:
            ip = ipaddress.ip_address(ip_str)
            return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
        except ValueError:
            return False

    def validate(self, payload: dict) -> None:
        """Enforces URL parsing, domain allowlists, and resolves host to block SSRF targets."""
        url = payload.get("url")
        if not url:
            raise ValueError("Webhook validation failed: 'url' parameter is required")
        if not payload.get("data") and not payload.get("webhook_payload"):
            raise ValueError("Webhook validation failed: payload 'data' cannot be completely empty")

        try:
            parsed_url = urlparse(url)
            hostname = parsed_url.hostname
            if not hostname:
                raise ValueError("Invalid URL format: missing hostname")

            # 1. Domain allowlist check
            if settings.allowed_webhook_domains:
                if hostname not in settings.allowed_webhook_domains:
                    raise ValueError(
                        f"Target domain '{hostname}' is not registered in the webhook allowlist settings"
                    )

            # 2. SSRF check resolving DNS targets
            if not settings.allow_private_webhooks:
                addr_info = socket.getaddrinfo(hostname, None)
                for family, socktype, proto, canonname, sockaddr in addr_info:
                    ip = sockaddr[0]
                    if self._is_private_ip(ip):
                        raise ValueError(
                            f"SSRF Protection: Outbound connection blocked to local/private target: {ip}"
                        )
        except Exception as e:
            if isinstance(e, ValueError):
                raise e
            raise ValueError(f"Webhook URL validation failed: {str(e)}")

    def execute(self, payload: dict) -> ActionResult:
        """Dispatches an outbound HTTP POST request containing JSON data blocks."""
        start_time = time.perf_counter()
        self.validate(payload)

        url = payload["url"]
        data = payload.get("data") or payload.get("webhook_payload")

        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            # Dispatch outbound connection under timeout
            with urllib.request.urlopen(req, timeout=5.0) as response:
                status_code = response.getcode()
                response_body = response.read().decode("utf-8")
                duration_ms = (time.perf_counter() - start_time) * 1000.0

                return ActionResult(
                    success=True,
                    action_type="POST_WEBHOOK",
                    external_id=str(status_code),
                    details={
                        "url": url,
                        "status_code": status_code,
                        "response_body": response_body[:500],
                        "info": "Webhook successfully posted."
                    },
                    duration_ms=duration_ms
                )
        except urllib.error.HTTPError as he:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ActionResult(
                success=False,
                action_type="POST_WEBHOOK",
                external_id=str(he.code),
                details={
                    "url": url,
                    "status_code": he.code,
                    "error": str(he),
                    "response_body": he.read().decode("utf-8", errors="ignore")[:500]
                },
                duration_ms=duration_ms
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ActionResult(
                success=False,
                action_type="POST_WEBHOOK",
                external_id=None,
                details={
                    "url": url,
                    "error": f"Connection/DNS failure: {str(e)}"
                },
                duration_ms=duration_ms
            )
