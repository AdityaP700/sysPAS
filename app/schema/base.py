from abc import ABC, abstractmethod
from typing import List
from app.schema.models import SchemaContext, SchemaDiscoveryResult


class BaseSchemaProvider(ABC):
    """Abstract Base Class for schema lookup, indexing, and validation checks."""

    @abstractmethod
    def get_fields(self, data_source: str) -> List[str]:
        """
        Retrieves the list of available field names for a specific data source.
        """
        pass

    @abstractmethod
    def get_indexes(self) -> List[str]:
        """
        Retrieves all valid data source indexes available.
        """
        pass

    @abstractmethod
    def validate_field(self, data_source: str, field_name: str) -> bool:
        """
        Verifies if a field name exists in the schema of a data source.
        """
        pass

    @abstractmethod
    def get_schema_summary(self, data_source: str) -> SchemaContext:
        """
        Summarizes schema structure and returns details inside a SchemaContext payload.
        """
        pass

    @abstractmethod
    def discover_schema(self, data_source: str) -> SchemaDiscoveryResult:
        """
        Discovers index schema parameters dynamically from telemetry endpoints.
        """
        pass
