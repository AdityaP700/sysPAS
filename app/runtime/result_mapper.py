import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.runtime.query_results import QueryResult
from app.audit.models import AuditEventRecord


class ResultMapper:
    """Service mapping query rows and nested JSON payloads into execution context variables."""

    def __init__(self, max_query_rows: int = 1000, max_query_payload_bytes: int = 1048576):
        self.max_query_rows = max_query_rows
        self.max_query_payload_bytes = max_query_payload_bytes

    def flatten_dict(self, d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
        """Recursively flattens nested dictionaries, joining keys with '.'."""
        items = {}
        for k, v in d.items():
            new_key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
            if isinstance(v, dict):
                items.update(self.flatten_dict(v, new_key))
            else:
                items[new_key] = v
        return items

    def map_results(
        self,
        tenant_id: str,
        execution_id: str,
        query_result: QueryResult,
        context: Dict[str, Any],
        audit_repo: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Processes and flattens query results into context.
        Enforces limits on row count and total JSON payload size.
        """
        rows = query_result.rows
        truncated = False
        reason = None

        # 1. Row count constraint
        if len(rows) > self.max_query_rows:
            rows = rows[:self.max_query_rows]
            truncated = True
            reason = "row_limit"

        # 2. Byte payload constraint
        # Progressively pop rows until serialized size is under threshold
        while len(rows) > 0:
            serialized = json.dumps(rows)
            byte_size = len(serialized.encode("utf-8"))
            if byte_size <= self.max_query_payload_bytes:
                break
            rows.pop()
            truncated = True
            reason = "byte_limit"

        # Update metadata if truncation occurred
        if truncated:
            query_result.metadata["truncated"] = True
            query_result.metadata["truncation_reason"] = reason
            query_result.row_count = len(rows)
            query_result.rows = rows

            # Write QUERY_TRUNCATED audit event
            if audit_repo:
                try:
                    audit_record = AuditEventRecord(
                        audit_id=str(uuid.uuid4()),
                        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        request_id=str(uuid.uuid4()),
                        correlation_id=str(uuid.uuid4()),
                        user_id="system",
                        role="SYSTEM",
                        action="QUERY_TRUNCATED",
                        resource_type="execution",
                        resource_id=execution_id,
                        status="WARNING",
                        details={
                            "reason": reason,
                            "original_count": len(query_result.rows) if not truncated else len(query_result.rows) + 1,
                            "final_count": len(rows),
                            "max_rows": self.max_query_rows,
                            "max_bytes": self.max_query_payload_bytes
                        },
                        tenant_id=tenant_id
                    )
                    audit_repo.save_audit_event(tenant_id, audit_record)
                except Exception:
                    pass

        # 3. Store normalized list arrays in context
        context["rows"] = rows
        context["results"] = rows
        context["row_count"] = len(rows)

        # 4. Flatten first row to root execution context
        flattened_vars = {}
        if rows:
            first_row = rows[0]
            flattened_vars = self.flatten_dict(first_row)
            for k, v in flattened_vars.items():
                context[k] = v

        return flattened_vars
