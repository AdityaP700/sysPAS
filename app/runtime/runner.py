import json
import re
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import anyio

from app.config.settings import settings
from app.splunk.adapters.client import call_mcp_tool_async, run_async
from app.runtime.query_results import QueryResult, QueryExecutionError


class BaseQueryRunner(ABC):
    """Abstract interface for executing SPL queries inside RunbookMind agent workflows."""

    @abstractmethod
    def run_query(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a query and returns a dictionary of output state variables."""
        pass


class MockQueryRunner(BaseQueryRunner):
    """
    Simulated query runner that evaluates input contexts and query strings
    to return mock search result variables.
    """

    def run_query(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        q_lower = query.lower()

        # Heuristics for authentication/brute force step
        if "failures" in q_lower or "auth_logs" in q_lower or "failed" in q_lower:
            failures_input = context.get("failures")
            if failures_input is not None:
                try:
                    return {"failures": int(failures_input)}
                except ValueError:
                    pass
            return {"failures": 120}

        # Heuristics for IP source checks
        if "source_ip" in q_lower or "ip" in q_lower:
            ip_input = context.get("source_ip")
            if ip_input is not None:
                return {"source_ip": str(ip_input), "is_internal": ip_input == "internal"}
            return {"source_ip": "external", "is_internal": False}

        # Heuristics for block action outcomes
        if "block" in q_lower:
            return {"blocked": True, "status": "success"}

        return {"status": "success", "count": 1}


class SplunkQueryRunner(BaseQueryRunner):
    """Real query runner executing searches against Splunk cluster via MCP server."""

    def __init__(self, repo: Optional[Any] = None, audit_repo: Optional[Any] = None):
        self.repo = repo
        self.audit_repo = audit_repo

    async def _call_mcp_with_timeout(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Helper to call MCP client tool with anyio timeout."""
        timeout_val = float(settings.query_timeout_seconds)
        with anyio.fail_after(timeout_val):
            return await call_mcp_tool_async(tool_name, arguments)

    def run_query_detailed(self, query: str, context: Dict[str, Any], tenant_id: str = "system", **kwargs) -> QueryResult:
        """Executes search and returns a full QueryResult object with metadata."""
        query_clean = query.strip()
        start_time = time.perf_counter()

        # 1. Determine if this is a saved search name
        # Saved search contains no spaces, operators, or symbols
        is_saved_search = not any(c in query_clean for c in (" ", "|", "=", "[", "]", "*"))

        if is_saved_search:
            # Validate name format
            if not re.match(r"^[a-zA-Z0-9_-]+$", query_clean):
                raise QueryExecutionError(f"Validation failed: Invalid saved search name format '{query_clean}'")
            tool_name = "splunk_run_saved_search"
            arguments = {"name": query_clean}
        else:
            tool_name = "splunk_run_query"
            arguments = {"query": query_clean}

        # 2. Extract Splunk parameter overrides from context and kwargs
        for k in ("earliest_time", "latest_time", "splunk_earliest_time", "splunk_latest_time", "output_mode"):
            if k in context:
                clean_key = k[7:] if k.startswith("splunk_") else k
                arguments[clean_key] = context[k]
        arguments.update(kwargs)

        # 3. Resolve splunk_secret if present
        splunk_secret_name = arguments.pop("splunk_secret", None) or context.get("splunk_secret")
        if splunk_secret_name:
            try:
                from app.vault.service import VaultService
                if not self.repo:
                    from app.web.dependencies import get_sqlite_repository
                    self.repo = get_sqlite_repository()
                if not self.repo:
                    raise ValueError("Repository not available for secret resolution.")

                vault_service = VaultService(self.repo)
                decrypted_token = vault_service.resolve_secret(tenant_id, splunk_secret_name)
                
                # Replace/set token parameters
                arguments["splunk_token"] = decrypted_token
                arguments["token"] = decrypted_token
            except Exception as e:
                # Log SECRET_RESOLUTION_FAILED audit event
                if self.audit_repo:
                    from app.audit.models import AuditEventRecord
                    import uuid
                    from datetime import datetime, timezone
                    audit_record = AuditEventRecord(
                        audit_id=str(uuid.uuid4()),
                        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        request_id=str(uuid.uuid4()),
                        correlation_id=str(uuid.uuid4()),
                        user_id="system",
                        role="SYSTEM",
                        action="SECRET_RESOLUTION_FAILED",
                        resource_type="secret",
                        resource_id=splunk_secret_name,
                        status="ERROR",
                        details={"error": str(e), "secret_name": splunk_secret_name},
                        tenant_id=tenant_id
                    )
                    self.audit_repo.save_audit_event(tenant_id, audit_record)
                raise QueryExecutionError(f"Validation failed: Secret resolution failed for '{splunk_secret_name}': {str(e)}") from e

        # Ensure tenant_id is not passed to the MCP tool
        arguments.pop("tenant_id", None)

        try:
            # Run the tool under timeout
            result_str = run_async(self._call_mcp_with_timeout, tool_name, arguments)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            # Parse search results
            data = json.loads(result_str)
            rows = []
            metadata = {}

            if isinstance(data, list):
                rows = data
            elif isinstance(data, dict):
                if "results" in data and isinstance(data["results"], list):
                    rows = data["results"]
                elif "rows" in data and isinstance(data["rows"], list):
                    rows = data["rows"]
                else:
                    rows = [data]
                metadata = {k: v for k, v in data.items() if k not in ("results", "rows")}

            return QueryResult(
                success=True,
                row_count=len(rows),
                rows=rows,
                metadata=metadata,
                duration_ms=duration_ms,
            )
        except TimeoutError as te:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            raise QueryExecutionError(f"Query execution timed out after {settings.query_timeout_seconds}s") from te
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            raise QueryExecutionError(f"Query execution failed: {str(e)}") from e

    def run_query(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Runs the query and returns flat output dictionary mapping variables."""
        # For compatibility, returns flat variables list
        try:
            res = self.run_query_detailed(query, context)
            if res.rows:
                # Returns the first row flat variables
                from app.runtime.result_mapper import ResultMapper
                mapper = ResultMapper()
                return mapper.flatten_dict(res.rows[0])
            return {}
        except Exception as e:
            # In compatibility mode, return empty or bubble up
            raise QueryExecutionError(str(e)) from e

