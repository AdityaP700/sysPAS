import pytest
import os
import tempfile
from unittest.mock import MagicMock
from app.runtime.result_mapper import ResultMapper
from app.runtime.query_results import QueryResult
from app.audit.repository import SQLiteAuditRepository


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_query_limits_row_and_byte_bounds(temp_db):
    audit_repo = SQLiteAuditRepository(temp_db)
    
    # 1. Test Row Limit (max 5 rows)
    mapper_row = ResultMapper(max_query_rows=5, max_query_payload_bytes=1000)
    query_result_row = QueryResult(
        success=True,
        row_count=10,
        rows=[{"id": i} for i in range(10)],
        metadata={},
        duration_ms=5.0
    )
    
    context = {}
    mapper_row.map_results("tenant-1", "exec-1", query_result_row, context, audit_repo)
    
    assert query_result_row.metadata.get("truncated") is True
    assert query_result_row.metadata.get("truncation_reason") == "row_limit"
    assert len(context["rows"]) == 5
    
    # Check that audit log was generated
    events = audit_repo.list_audit_events("tenant-1")
    assert len(events) == 1
    assert events[0].action == "QUERY_TRUNCATED"

    # 2. Test Byte Limit (max 100 bytes)
    mapper_byte = ResultMapper(max_query_rows=10, max_query_payload_bytes=100)
    query_result_byte = QueryResult(
        success=True,
        row_count=10,
        rows=[{"id": i, "data": "abcdefghijklmnop"} for i in range(10)],
        metadata={},
        duration_ms=5.0
    )
    
    context_byte = {}
    mapper_byte.map_results("tenant-1", "exec-2", query_result_byte, context_byte, audit_repo)
    
    assert query_result_byte.metadata.get("truncated") is True
    assert query_result_byte.metadata.get("truncation_reason") == "byte_limit"
    
    import json
    serialized = json.dumps(context_byte["rows"])
    assert len(serialized.encode("utf-8")) <= 100
    
    # Check that another audit event was generated
    events_all = audit_repo.list_audit_events("tenant-1")
    assert len(events_all) == 2
    assert events_all[0].action == "QUERY_TRUNCATED"
