import json
import logging
import sys
from datetime import datetime, timezone
from app.observability.request_context import get_request_id, get_correlation_id, get_user_id, get_role, get_tenant_id


class JSONFormatter(logging.Formatter):
    """Custom formatter to output structured JSON logs with mandatory telemetry fields."""

    def format(self, record: logging.LogRecord) -> str:
        # Resolve request context values dynamically
        req_id = get_request_id()
        corr_id = get_correlation_id()
        usr_id = get_user_id()
        role = get_role()
        tenant_id = get_tenant_id()

        # Fallbacks if logged via extra={'request_id': ...} etc.
        if not req_id and "request_id" in record.__dict__:
            req_id = record.__dict__["request_id"]
        if not corr_id and "correlation_id" in record.__dict__:
            corr_id = record.__dict__["correlation_id"]

        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": "runbookmind",
            "message": record.getMessage(),
            "request_id": req_id,
            "correlation_id": corr_id,
            "user_id": usr_id,
            "role": role,
            "tenant_id": tenant_id,
            "component": record.__dict__.get("component", "compiler"),
            "operation": record.__dict__.get("operation", "execute"),
            "duration_ms": record.__dict__.get("duration_ms"),
            "status": record.__dict__.get("status")
        }
        
        # Filter out None values to keep records clean
        clean_log = {k: v for k, v in log_data.items() if v is not None}
        return json.dumps(clean_log)


# Initialize and configure the logger
logger = logging.getLogger("runbookmind.observability")
logger.setLevel(logging.INFO)

# Avoid adding duplicate handlers if the module is re-imported
if not logger.handlers:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(JSONFormatter())
    logger.addHandler(stream_handler)
    logger.propagate = False
