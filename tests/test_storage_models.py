import pytest
from pydantic import ValidationError
from app.storage.models import BundleRecord, CompilationRecord, TraceRecord


def test_bundle_record_validation():
    """Verify that BundleRecord correctly validates fields and raises error on missing fields."""
    valid_data = {
        "bundle_id": "test-uuid-123",
        "bundle_name": "Test Bundle",
        "version": 1,
        "created_at": "2026-06-12T00:00:00Z",
        "status": "SUCCESS",
        "payload": {"manifest": {}, "agent_skill": {}, "diagnostics": {}, "traces": []},
    }
    record = BundleRecord(**valid_data)
    assert record.bundle_id == "test-uuid-123"
    assert record.version == 1
    assert record.payload["traces"] == []

    # Missing payload
    invalid_data = valid_data.copy()
    del invalid_data["payload"]
    with pytest.raises(ValidationError):
        BundleRecord(**invalid_data)


def test_compilation_record_validation():
    """Verify that CompilationRecord validates parameters properly."""
    valid_data = {
        "compilation_id": "comp-uuid-456",
        "bundle_id": "test-uuid-123",
        "timestamp": "2026-06-12T00:00:01Z",
        "duration_ms": 120.5,
        "confidence": 0.95,
        "status": "SUCCESS",
    }
    record = CompilationRecord(**valid_data)
    assert record.compilation_id == "comp-uuid-456"
    assert record.duration_ms == 120.5

    # Incorrect type for duration
    invalid_data = valid_data.copy()
    invalid_data["duration_ms"] = "not-a-float"
    with pytest.raises(ValidationError):
        CompilationRecord(**invalid_data)


def test_trace_record_validation():
    """Verify that TraceRecord schema behaves as expected including optional fields."""
    valid_data = {
        "trace_id": "trace-uuid-789",
        "compilation_id": "comp-uuid-456",
        "step_id": "step_1",
        "request_id": "req-1",
        "correlation_id": "corr-1",
        "payload": {"step_id": "step_1", "overall_confidence": 0.8},
    }
    record = TraceRecord(**valid_data)
    assert record.trace_id == "trace-uuid-789"
    assert record.request_id == "req-1"

    # Verify optional fields default to None
    no_ids_data = {
        "trace_id": "trace-uuid-789",
        "compilation_id": "comp-uuid-456",
        "step_id": "step_1",
        "payload": {},
    }
    record_no_ids = TraceRecord(**no_ids_data)
    assert record_no_ids.request_id is None
    assert record_no_ids.correlation_id is None
