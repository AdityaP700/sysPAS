import os
import tempfile
import pytest
from app.audit.models import AuditEventRecord
from app.audit.repository import SQLiteAuditRepository


@pytest.fixture
def temp_db_file():
    """Create a temporary database file and clean it up after the test completes."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_sqlite_audit_logging_and_pagination(temp_db_file):
    """Test saving audit logs, listing logs, sorting by timestamp DESC, and limit/offset pagination."""
    repo = SQLiteAuditRepository(temp_db_file)

    # 1. Insert 5 audit event records
    for i in range(5):
        record = AuditEventRecord(
            audit_id=f"audit-uuid-{i}",
            timestamp=f"2026-06-12T00:00:{i:02d}Z",
            request_id=f"req-{i}",
            correlation_id=f"corr-{i}",
            user_id="operator_key",
            role="OPERATOR",
            action="COMPILE_RUNBOOK",
            resource_type="runbook",
            resource_id=f"runbook_{i}.md",
            status="SUCCESS",
            details={"index": i},
        )
        repo.save_audit_event(record)

    # 2. List all (ordered by timestamp DESC)
    all_logs = repo.list_audit_events(limit=10, offset=0)
    assert len(all_logs) == 5
    assert all_logs[0].audit_id == "audit-uuid-4"  # latest first
    assert all_logs[4].audit_id == "audit-uuid-0"

    # 3. Test limit pagination
    limited_logs = repo.list_audit_events(limit=2, offset=0)
    assert len(limited_logs) == 2
    assert limited_logs[0].audit_id == "audit-uuid-4"
    assert limited_logs[1].audit_id == "audit-uuid-3"

    # 4. Test offset pagination
    offset_logs = repo.list_audit_events(limit=2, offset=2)
    assert len(offset_logs) == 2
    assert offset_logs[0].audit_id == "audit-uuid-2"
    assert offset_logs[1].audit_id == "audit-uuid-1"
