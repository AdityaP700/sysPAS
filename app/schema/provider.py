from typing import List, Dict
from app.schema.base import BaseSchemaProvider, SchemaContext, SchemaDiscoveryResult


class MockSchemaProvider(BaseSchemaProvider):
    """Mock Schema Provider returning static metadata mapping for testing."""

    def __init__(self):
        self._schema_map: Dict[str, List[str]] = {
            "auth_logs": ["src_ip", "user", "action", "status"],
            "threat_intel": ["ip_address", "threat_score", "category"],
            "sales_metrics": ["store_id", "revenue_drop_pct", "date"],
            "main": ["host", "user", "process", "parent_process", "action", "severity", "status", "src_ip"]
        }

    def get_fields(self, data_source: str) -> List[str]:
        return self._schema_map.get(data_source, [])

    def get_indexes(self) -> List[str]:
        return list(self._schema_map.keys())

    def validate_field(self, data_source: str, field_name: str) -> bool:
        fields = self.get_fields(data_source)
        return field_name in fields

    def get_schema_summary(self, data_source: str) -> SchemaContext:
        fields = self.get_fields(data_source)
        return SchemaContext(data_source=data_source, fields=fields)

    def discover_schema(self, data_source: str) -> SchemaDiscoveryResult:
        is_known = data_source in self._schema_map
        fields = self.get_fields(data_source)
        return SchemaDiscoveryResult(
            data_source=data_source,
            fields=fields,
            is_successful=is_known
        )
