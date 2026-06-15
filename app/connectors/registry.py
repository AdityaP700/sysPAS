from typing import Dict, Type, Optional
from app.connectors.models import ConnectorType
from app.connectors.base import BaseConnector


class ConnectorRegistry:
    """Marketplace connector class mapping registry registry."""

    def __init__(self):
        self._types: Dict[ConnectorType, Type[BaseConnector]] = {}

    def register(self, connector_type: ConnectorType, connector_class: Type[BaseConnector]) -> None:
        self._types[connector_type] = connector_class

    def get(self, connector_type: ConnectorType) -> Optional[Type[BaseConnector]]:
        return self._types.get(connector_type)


connector_registry = ConnectorRegistry()
