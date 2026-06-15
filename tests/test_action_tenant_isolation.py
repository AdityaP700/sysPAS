import pytest
import os
import tempfile
from app.storage.sqlite import SQLiteRepository
from app.runtime.models import ActionExecutionRecord


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_action_tenant_isolation(temp_db):
    repo = SQLiteRepository(temp_db)
    
    # 1. Create a successful action execution record in Tenant A
    rec_a = ActionExecutionRecord(
        action_execution_id="act-a",
        tenant_id="tenant-a",
        execution_id="exec-a",
        node_id="node-1",
        action_type="SEND_EMAIL",
        external_id="ext-a",
        success=True,
        duration_ms=10.0,
        payload={"data": 123},
        idempotency_key="key-idempotent-123",
        created_at="2026-06-13T10:00:00Z"
    )
    repo.save_action_execution("tenant-a", rec_a)
    
    # 2. Query Tenant A action executions -> should find it
    list_a = repo.get_action_executions("tenant-a", "exec-a")
    assert len(list_a) == 1
    assert list_a[0].action_execution_id == "act-a"
    
    # 3. Query Tenant B action executions for the same execution_id -> should be empty (since tenant scope is different)
    list_b = repo.get_action_executions("tenant-b", "exec-a")
    assert len(list_b) == 0

    # 4. Check successful action execution by idempotency key
    # In Tenant A -> should find it
    found_a = repo.get_successful_action_execution("tenant-a", "key-idempotent-123")
    assert found_a is not None
    assert found_a.action_execution_id == "act-a"

    # In Tenant B -> should not find it (cross-tenant safety check)
    found_b = repo.get_successful_action_execution("tenant-b", "key-idempotent-123")
    assert found_b is None
