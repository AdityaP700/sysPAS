import pytest
from app.web.routes import redact_credentials


def test_redact_credentials_simple():
    data = {
        "username": "admin",
        "password": "my-secret-password",
        "api_key": "key-xyz",
        "secret_token": "some-token-value"
    }
    redacted = redact_credentials(data)
    assert redacted["username"] == "admin"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["secret_token"] == "[REDACTED]"


def test_redact_credentials_nested():
    data = {
        "meta": {
            "auth": {
                "Authorization": "Bearer tok-12345",
                "client_secret": "secret123"
            },
            "status": "active"
        },
        "items": [
            {"name": "item1", "api_key": "key1"},
            {"name": "item2", "token": "tok2"}
        ]
    }
    redacted = redact_credentials(data)
    assert redacted["meta"]["status"] == "active"
    assert redacted["meta"]["auth"]["Authorization"] == "[REDACTED]"
    assert redacted["meta"]["auth"]["client_secret"] == "[REDACTED]"
    assert redacted["items"][0]["api_key"] == "[REDACTED]"
    assert redacted["items"][0]["name"] == "item1"
    assert redacted["items"][1]["token"] == "[REDACTED]"
