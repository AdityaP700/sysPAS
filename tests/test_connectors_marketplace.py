import pytest
import os
import tempfile
import time
from unittest.mock import MagicMock

from app.storage.sqlite import SQLiteRepository
from app.connectors.models import ConnectorType, ConnectorRecord
from app.connectors.service import ConnectorService
from app.connectors.base import RateLimitExceededError, CircuitBreakerOpenError


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Run migrations/create tables by instantiating repo
    repo = SQLiteRepository(path)
    # Add a mock tenant to list_tenants
    from app.auth.models import TenantRecord
    repo.save_tenant(TenantRecord(tenant_id="tenant-1", name="Tenant 1", slug="tenant-1", created_at="2026-06-13T00:00:00Z"))
    yield repo
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_connector_creation_and_versioning(temp_db):
    service = ConnectorService(temp_db)
    tenant_id = "tenant-1"

    # 1. Create a Slack connector
    record = service.create_connector(
        tenant_id=tenant_id,
        connector_type=ConnectorType.SLACK,
        name="Slack Ops",
        configuration={"bot_token": "mock-slack-token", "default_channel": "#alerts"},
        description="Slack notifications"
    )

    assert record.connector_id.startswith("conn_")
    assert record.connector_version == 1
    assert record.health_status == "HEALTHY"
    assert record.validation_error is None

    # Verify stored in DB
    stored = service.get_connector(tenant_id, record.connector_id)
    assert stored is not None
    assert stored.name == "Slack Ops"
    assert stored.connector_version == 1

    # 2. Update config -> creates version 2
    updated = service.update_connector(
        tenant_id=tenant_id,
        connector_id=record.connector_id,
        name="Slack Ops Updated",
        configuration={"bot_token": "mock-slack-token-v2", "default_channel": "#incidents"}
    )

    assert updated.connector_version == 2
    assert updated.name == "Slack Ops Updated"
    assert updated.configuration["bot_token"] == "mock-slack-token-v2"

    # Retrieve latest version (version=None)
    latest = service.get_connector(tenant_id, record.connector_id)
    assert latest.connector_version == 2
    assert latest.name == "Slack Ops Updated"

    # Retrieve version 1 specifically
    v1 = service.get_connector(tenant_id, record.connector_id, version=1)
    assert v1.connector_version == 1
    assert v1.name == "Slack Ops"


def test_connector_credentials_validation_failure(temp_db):
    service = ConnectorService(temp_db)
    tenant_id = "tenant-1"

    # Creation fails with ValueError if token is invalid
    with pytest.raises(ValueError) as exc:
        service.create_connector(
            tenant_id=tenant_id,
            connector_type=ConnectorType.SLACK,
            name="Broken Slack",
            configuration={"bot_token": "invalid_token"}
        )
    assert "validation error" in str(exc.value)


def test_connector_sandbox_test(temp_db):
    service = ConnectorService(temp_db)
    tenant_id = "tenant-1"

    # Create Slack connector
    record = service.create_connector(
        tenant_id=tenant_id,
        connector_type=ConnectorType.SLACK,
        name="Slack Ops",
        configuration={"bot_token": "mock-slack-token", "default_channel": "#alerts"}
    )

    # Test connection
    res = service.test_connector(tenant_id, record.connector_id)
    assert res["success"] is True
    assert res["error"] is None

    # Change token to invalid in configuration (manually update db record version 1 to simulate backend/rest error)
    record.configuration["bot_token"] = "invalid_token"
    temp_db.save_connector(tenant_id, record)

    # Sandbox test should now return failure
    res_fail = service.test_connector(tenant_id, record.connector_id)
    assert res_fail["success"] is False
    assert "failed" in res_fail["error"].lower()


def test_connector_rate_limiting(temp_db):
    service = ConnectorService(temp_db)
    tenant_id = "tenant-1"

    # Create connector with rate limit of 2 calls/minute
    record = service.create_connector(
        tenant_id=tenant_id,
        connector_type=ConnectorType.SLACK,
        name="Slack Limited",
        configuration={"bot_token": "mock-slack-token"},
        rate_limit_per_minute=2
    )

    connector_instance = service._get_connector_instance(tenant_id, record)

    # First and second call pass
    connector_instance.execute({"text": "Hello 1"})
    connector_instance.execute({"text": "Hello 2"})

    # Third call raises RateLimitExceededError
    with pytest.raises(RateLimitExceededError):
        connector_instance.execute({"text": "Hello 3"})


def test_connector_circuit_breaker_transitions(temp_db):
    service = ConnectorService(temp_db)
    tenant_id = "tenant-1"

    # We manually set up a custom connector or mock it.
    # To test BaseConnector's circuit breaker, we can use Slack connector but mock its execute method to fail.
    record = service.create_connector(
        tenant_id=tenant_id,
        connector_type=ConnectorType.SLACK,
        name="Slack Breaker",
        configuration={"bot_token": "mock-slack-token"}
    )

    # Retrieve and force connector to use a failing bot token
    record.configuration["bot_token"] = "invalid_token"
    temp_db.save_connector(tenant_id, record)

    connector_instance = service._get_connector_instance(tenant_id, record)
    assert connector_instance.record.circuit_state == "CLOSED"

    # Execute and fail consecutive times
    for _ in range(5):
        try:
            connector_instance.execute({"text": "Alert"})
        except ValueError:
            pass

    # Verify circuit breaker transitioned to OPEN
    stored_rec = service.get_connector(tenant_id, record.connector_id)
    assert stored_rec.circuit_state == "OPEN"
    assert stored_rec.circuit_opened_at is not None

    # Subsequent execution raises CircuitBreakerOpenError immediately
    with pytest.raises(CircuitBreakerOpenError):
        connector_instance.execute({"text": "Fail Fast"})
