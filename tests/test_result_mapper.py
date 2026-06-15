import pytest
from unittest.mock import MagicMock
from app.runtime.result_mapper import ResultMapper
from app.runtime.query_results import QueryResult


def test_result_mapper_flatten_dict():
    mapper = ResultMapper()
    nested = {
        "a": 1,
        "b": {
            "c": 2,
            "d": {
                "e": 3
            }
        }
    }
    flattened = mapper.flatten_dict(nested)
    assert flattened == {
        "a": 1,
        "b.c": 2,
        "b.d.e": 3
    }


def test_result_mapper_row_truncation():
    mapper = ResultMapper(max_query_rows=3)
    query_result = QueryResult(
        success=True,
        row_count=5,
        rows=[{"id": i} for i in range(5)],
        metadata={},
        duration_ms=10.0
    )
    
    context = {}
    audit_repo = MagicMock()
    
    flat = mapper.map_results("tenant-1", "exec-123", query_result, context, audit_repo)
    
    assert query_result.metadata.get("truncated") is True
    assert query_result.metadata.get("truncation_reason") == "row_limit"
    assert len(context["rows"]) == 3
    assert len(flat) == 1
    assert flat["id"] == 0  # First row is flattened
    audit_repo.save_audit_event.assert_called_once()


def test_result_mapper_byte_truncation():
    # Set maximum byte size to a small value (e.g. 50 bytes)
    mapper = ResultMapper(max_query_rows=10, max_query_payload_bytes=50)
    # A single row is around 15 bytes serialized
    rows = [{"id": i, "val": "xyz"} for i in range(5)]
    query_result = QueryResult(
        success=True,
        row_count=5,
        rows=rows,
        metadata={},
        duration_ms=10.0
    )
    
    context = {}
    audit_repo = MagicMock()
    
    flat = mapper.map_results("tenant-1", "exec-123", query_result, context, audit_repo)
    
    assert query_result.metadata.get("truncated") is True
    assert query_result.metadata.get("truncation_reason") == "byte_limit"
    # Ensure it serialized under 50 bytes
    import json
    serialized = json.dumps(context["rows"])
    assert len(serialized.encode("utf-8")) <= 50
    audit_repo.save_audit_event.assert_called_once()
