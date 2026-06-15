import json
from typing import List, Optional
from app.schema.base import BaseSchemaProvider
from app.schema.models import SchemaContext, SchemaDiscoveryResult
from app.schema.cache import SchemaCache
from app.splunk.adapters.client import call_mcp_tool
from app.observability.logging import logger
from app.config.settings import settings


class SchemaDiscoveryEngine(BaseSchemaProvider):
    """Dynamic schema provider that discovers indexes and fields using MCP tools, backed by SchemaCache."""

    def __init__(self, cache: Optional[SchemaCache] = None):
        self.cache = cache if cache is not None else SchemaCache()

    def get_fields(self, data_source: str) -> List[str]:
        """Retrieves field names for an index, checking cache first before calling Splunk MCP."""
        # 1. Check cache
        cached_fields = self.cache.get(data_source)
        if cached_fields is not None:
            return cached_fields

        # 2. Cache miss - Call configured MCP tool for retrieving fields
        try:
            response = call_mcp_tool(settings.mcp_tool_get_fields, {"index": data_source})
            try:
                data = json.loads(response)
                fields = data.get("fields", [])
            except json.JSONDecodeError:
                # If plain text return, split by comma or newline
                fields = [f.strip() for f in response.replace("\n", ",").split(",") if f.strip()]
            
            # Cache the discovered fields
            self.cache.set(data_source, fields)
            return fields
        except Exception as e:
            logger.warning(
                f"Failed to discover fields via MCP for index '{data_source}': {str(e)}",
                extra={"component": "schema", "operation": "get_fields", "status": "failed"}
            )
            return []

    def get_indexes(self) -> List[str]:
        """Retrieves all valid data source indexes available via Splunk MCP."""
        try:
            response = call_mcp_tool(settings.mcp_tool_get_indexes, {})
            try:
                data = json.loads(response)
                return data.get("indexes", [])
            except json.JSONDecodeError:
                return [idx.strip() for idx in response.replace("\n", ",").split(",") if idx.strip()]
        except Exception as e:
            logger.warning(
                f"Failed to discover indexes via MCP: {str(e)}",
                extra={"component": "schema", "operation": "get_indexes", "status": "failed"}
            )
            return []

    def validate_field(self, data_source: str, field_name: str) -> bool:
        """Verifies if a field name exists in the schema of a data source."""
        fields = self.get_fields(data_source)
        return field_name in fields

    def get_schema_summary(self, data_source: str) -> SchemaContext:
        """Returns details inside a SchemaContext payload."""
        fields = self.get_fields(data_source)
        return SchemaContext(data_source=data_source, fields=fields)

    def discover_schema(self, data_source: str) -> SchemaDiscoveryResult:
        """Scan and discover index schema parameters dynamically from Splunk MCP."""
        # Force refresh/invalidation of cache entry to guarantee real discovery
        self.cache.invalidate(data_source)
        fields = self.get_fields(data_source)
        is_successful = len(fields) > 0
        return SchemaDiscoveryResult(
            data_source=data_source,
            fields=fields,
            is_successful=is_successful
        )
