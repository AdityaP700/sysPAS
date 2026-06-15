import pytest
from unittest.mock import patch
from app.actions.webhook import WebhookConnector
from app.config.settings import settings


def test_webhook_allowlist_domain_restricted():
    connector = WebhookConnector()
    
    # 1. Setup allowed domains in settings
    old_domains = settings.allowed_webhook_domains
    settings.allowed_webhook_domains = ["api.slack.com", "hooks.jira.com"]
    
    try:
        # Resolve domains to public IP to bypass SSRF check
        mock_addr = [(2, 1, 6, "", ("8.8.8.8", 80))]
        with patch("socket.getaddrinfo", return_value=mock_addr):
            
            # Domain in allowlist -> should validate successfully
            connector.validate({"url": "https://api.slack.com/services/hook", "data": {"test": 1}})
            connector.validate({"url": "https://hooks.jira.com/v1/update", "data": {"test": 1}})
            
            # Domain NOT in allowlist -> should raise ValueError
            with pytest.raises(ValueError) as exc:
                connector.validate({"url": "https://malicious-site.com/webhook", "data": {"test": 1}})
            assert "not registered in the webhook allowlist" in str(exc.value)
            
    finally:
        settings.allowed_webhook_domains = old_domains


def test_webhook_allowlist_domain_empty_unrestricted():
    connector = WebhookConnector()
    
    # Empty domains allowlist in settings
    old_domains = settings.allowed_webhook_domains
    settings.allowed_webhook_domains = []
    
    try:
        mock_addr = [(2, 1, 6, "", ("8.8.8.8", 80))]
        with patch("socket.getaddrinfo", return_value=mock_addr):
            # Should validate successfully since allowlist is empty
            connector.validate({"url": "https://malicious-site.com/webhook", "data": {"test": 1}})
            connector.validate({"url": "https://api.slack.com/services/hook", "data": {"test": 1}})
            
    finally:
        settings.allowed_webhook_domains = old_domains
