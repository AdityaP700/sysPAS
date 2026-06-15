import json
import logging
import io
import uuid
from fastapi.testclient import TestClient
from app.web.main import app
from app.observability.request_context import (
    get_request_id,
    get_correlation_id,
    set_request_id,
    set_correlation_id,
    request_id_var,
    correlation_id_var
)
from app.observability.metrics import metrics_collector
from app.observability.logging import JSONFormatter

client = TestClient(app)


def test_context_propagation():
    """Verify that request_id and correlation_id getters and setters correctly modify contextvars."""
    # Reset context first
    t_req = request_id_var.set(None)
    t_corr = correlation_id_var.set(None)

    try:
        assert get_request_id() is None
        assert get_correlation_id() is None

        req = "req-" + str(uuid.uuid4())
        corr = "corr-" + str(uuid.uuid4())

        set_request_id(req)
        set_correlation_id(corr)

        assert get_request_id() == req
        assert get_correlation_id() == corr
    finally:
        request_id_var.reset(t_req)
        correlation_id_var.reset(t_corr)


def test_json_logging_format():
    """Verify that structured logging outputs JSON format containing all required fields."""
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setFormatter(JSONFormatter())

    test_logger = logging.getLogger("runbookmind.test_observability_modified")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)

    req = "req-999"
    corr = "corr-888"

    # Set context variables
    token_req = set_request_id(req)
    token_corr = set_correlation_id(corr)

    try:
        test_logger.info(
            "Logging test message",
            extra={
                "component": "compiler",
                "operation": "compile_runbook",
                "duration_ms": 42.1,
                "status": "success"
            }
        )
    finally:
        request_id_var.reset(token_req)
        correlation_id_var.reset(token_corr)
        test_logger.removeHandler(handler)

    log_output = log_capture.getvalue().strip()
    log_json = json.loads(log_output)

    assert log_json["request_id"] == req
    assert log_json["correlation_id"] == corr
    assert log_json["level"] == "INFO"
    assert log_json["service"] == "runbookmind"
    assert log_json["message"] == "Logging test message"
    assert log_json["component"] == "compiler"
    assert log_json["operation"] == "compile_runbook"
    assert log_json["duration_ms"] == 42.1
    assert log_json["status"] == "success"
    assert "timestamp" in log_json


def test_metrics_updates():
    """Verify metrics collector records API counts, error counts, and compilation metrics correctly."""
    metrics_collector.reset()

    assert metrics_collector.api_request_count == 0
    assert metrics_collector.api_error_count == 0
    assert metrics_collector.compilation_count == 0
    assert metrics_collector.compilation_success_count == 0
    assert metrics_collector.compilation_failure_count == 0
    assert metrics_collector.average_duration_ms == 0.0

    metrics_collector.record_api_request()
    metrics_collector.record_api_request()
    metrics_collector.record_api_error()

    metrics_collector.record_compilation(success=True, duration_ms=10.0)
    metrics_collector.record_compilation(success=False, duration_ms=20.0)

    assert metrics_collector.api_request_count == 2
    assert metrics_collector.api_error_count == 1
    assert metrics_collector.compilation_count == 2
    assert metrics_collector.compilation_success_count == 1
    assert metrics_collector.compilation_failure_count == 1
    assert metrics_collector.average_duration_ms == 15.0


def test_middleware_request_ids_and_correlation_reuse():
    """Verify middleware generates unique request IDs and reuses incoming X-Correlation-ID headers."""
    incoming_corr_id = "reused-correlation-id-abc"

    # Make first request passing correlation ID
    headers = {"X-Correlation-ID": incoming_corr_id}
    response1 = client.get("/health", headers=headers)
    assert response1.status_code == 200
    
    req_id1 = response1.headers["X-Request-ID"]
    corr_id1 = response1.headers["X-Correlation-ID"]

    assert req_id1 is not None
    assert corr_id1 == incoming_corr_id

    # Make second request, verifying that request_id is fresh, but correlation_id remains if passed
    response2 = client.get("/health", headers=headers)
    assert response2.status_code == 200

    req_id2 = response2.headers["X-Request-ID"]
    corr_id2 = response2.headers["X-Correlation-ID"]

    assert req_id2 != req_id1  # request_id is always unique/fresh
    assert corr_id2 == incoming_corr_id  # correlation_id is reused
