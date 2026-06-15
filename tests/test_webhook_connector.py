import pytest
import socket
from unittest.mock import MagicMock, patch
from app.actions.webhook import WebhookConnector
from app.config.settings import settings


def test_webhook_validation_empty_url():
    connector = WebhookConnector()
    with pytest.raises(ValueError) as exc:
        connector.validate({"data": {}})
    assert "url' parameter is required" in str(exc.value)


def test_webhook_validation_empty_data():
    connector = WebhookConnector()
    with pytest.raises(ValueError) as exc:
        connector.validate({"url": "http://example.com"})
    assert "payload 'data' cannot be completely empty" in str(exc.value)


def test_webhook_ssrf_protection_private_ip():
    connector = WebhookConnector()
    # Mock socket resolution returning a loopback IP
    mock_addr = [(2, 1, 6, "", ("127.0.0.1", 80))]
    
    with patch("socket.getaddrinfo", return_value=mock_addr):
        # By default allow_private_webhooks is False
        old_allow = settings.allow_private_webhooks
        settings.allow_private_webhooks = False
        try:
            with pytest.raises(ValueError) as exc:
                connector.validate({"url": "http://localhost/webhook", "data": {"test": 1}})
            assert "SSRF Protection" in str(exc.value)
        finally:
            settings.allow_private_webhooks = old_allow


def test_webhook_ssrf_protection_disabled_for_test():
    connector = WebhookConnector()
    mock_addr = [(2, 1, 6, "", ("127.0.0.1", 80))]
    
    with patch("socket.getaddrinfo", return_value=mock_addr):
        old_allow = settings.allow_private_webhooks
        settings.allow_private_webhooks = True
        try:
            # Should not raise ValueError since private is allowed
            connector.validate({"url": "http://localhost/webhook", "data": {"test": 1}})
        finally:
            settings.allow_private_webhooks = old_allow


def test_webhook_execute_success():
    connector = WebhookConnector()
    
    mock_response = MagicMock()
    mock_response_context = mock_response.__enter__.return_value
    mock_response_context.getcode.return_value = 200
    mock_response_context.read.return_value = b"success-response-body"
    
    with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
        # Mock getaddrinfo to return public IP to bypass validation
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 80))]):
            res = connector.execute({"url": "http://google.com/webhook", "data": {"alert": "true"}})
            assert res.success is True
            assert res.external_id == "200"
            assert res.details["response_body"] == "success-response-body"
            mock_open.assert_called_once()
