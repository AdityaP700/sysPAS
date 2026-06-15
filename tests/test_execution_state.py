import os
import tempfile
import pytest
from app.storage.sqlite import SQLiteRepository
from app.runtime.models import ExecutionRecord, ExecutionStatus


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_execution_record_persistence(temp_db):
    repo = SQLiteRepository(temp_db)

    rec = ExecutionRecord(
        execution_id="exec_1",
        tenant_id="tenant-soc",
        bundle_id="bundle-1",
        bundle_version=2,
        status=ExecutionStatus.RUNNING,
        current_node_id="node-A",
        started_at="2026-06-12T00:00:00Z",
        triggered_by="key-1",
        context_payload={"var1": "val1", "failures": 120}
    )

    repo.save_execution("tenant-soc", rec)

    retrieved = repo.get_execution("tenant-soc", "exec_1")
    assert retrieved is not None
    assert retrieved.execution_id == "exec_1"
    assert retrieved.status == ExecutionStatus.RUNNING
    assert retrieved.context_payload == {"var1": "val1", "failures": 120}

    # Update status
    retrieved.status = ExecutionStatus.COMPLETED
    retrieved.completed_at = "2026-06-12T00:05:00Z"
    repo.save_execution("tenant-soc", retrieved)

    retrieved2 = repo.get_execution("tenant-soc", "exec_1")
    assert retrieved2.status == ExecutionStatus.COMPLETED
    assert retrieved2.completed_at == "2026-06-12T00:05:00Z"
