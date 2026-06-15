import pytest
from app.web.routes import redact_credentials


def test_redact_credentials_extended():
    # Verify new parameters (credential, private_key) are redacted
    data = {
        "ssh_private_key": "--- BEGIN RSA PRIVATE KEY --- ...",
        "api_credential": "super-secret-creds",
        "normal_field": "public-value",
        "nested": {
            "aws_private_key": "some-key",
            "db_credential": "username:password"
        }
    }
    redacted = redact_credentials(data)
    assert redacted["ssh_private_key"] == "[REDACTED]"
    assert redacted["api_credential"] == "[REDACTED]"
    assert redacted["normal_field"] == "public-value"
    assert redacted["nested"]["aws_private_key"] == "[REDACTED]"
    assert redacted["nested"]["db_credential"] == "[REDACTED]"
