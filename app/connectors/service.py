import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from app.storage.sqlite import SQLiteRepository
from app.connectors.models import ConnectorRecord, ConnectorType
from app.connectors.registry import connector_registry
from app.vault.service import VaultService


class ConnectorService:
    """Handles logic for connector creation, configuration versioning, updates, validation, and sandbox testing."""

    def __init__(self, repo: SQLiteRepository):
        self.repo = repo
        self.vault_service = VaultService(repo)

    def _resolve_config_secrets(self, tenant_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively traverses the config and resolves secrets ending in _secret from the vault."""
        resolved = {}
        for k, v in config.items():
            if isinstance(k, str) and k.endswith("_secret"):
                new_key = k[:-7]
                secret_name = v
                if secret_name:
                    try:
                        decrypted = self.vault_service.resolve_secret(tenant_id, secret_name)
                        resolved[new_key] = decrypted
                    except Exception as e:
                        raise ValueError(f"Secret resolution failed for '{secret_name}': {str(e)}") from e
                else:
                    resolved[new_key] = None
            elif isinstance(v, dict):
                resolved[k] = self._resolve_config_secrets(tenant_id, v)
            elif isinstance(v, list):
                resolved[k] = [
                    self._resolve_config_secrets(tenant_id, item) if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                resolved[k] = v
        return resolved

    def _get_connector_instance(self, tenant_id: str, record: ConnectorRecord):
        """Helper to instantiate a connector implementation with resolved configuration."""
        connector_class = connector_registry.get(record.connector_type)
        if not connector_class:
            raise ValueError(f"No connector implementation found for type {record.connector_type}")

        # Resolve secrets in the configuration
        resolved_config = self._resolve_config_secrets(tenant_id, record.configuration)
        
        # Create a shallow/deep copy with resolved config
        resolved_record = record.copy(update={"configuration": resolved_config})
        return connector_class(resolved_record, repo=self.repo)

    def create_connector(
        self,
        tenant_id: str,
        connector_type: ConnectorType,
        name: str,
        configuration: Dict[str, Any],
        description: Optional[str] = None,
        rate_limit_per_minute: int = 100
    ) -> ConnectorRecord:
        """Create a new connector and perform initial credential validation."""
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        connector_id = f"conn_{uuid.uuid4().hex[:12]}"

        record = ConnectorRecord(
            connector_id=connector_id,
            tenant_id=tenant_id,
            connector_type=connector_type,
            name=name,
            description=description,
            enabled=True,
            configuration=configuration,
            connector_version=1,
            schema_version=1,
            health_status="UNKNOWN",
            last_health_check=None,
            last_success_at=None,
            consecutive_failures=0,
            last_validation_at=None,
            validation_error=None,
            rate_limit_per_minute=rate_limit_per_minute,
            circuit_state="CLOSED",
            circuit_failure_count=0,
            circuit_opened_at=None,
            created_at=now_str,
            updated_at=now_str
        )

        # Validate credentials
        try:
            instance = self._get_connector_instance(tenant_id, record)
            valid = instance.validate_credentials()
            record.last_validation_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if valid:
                record.validation_error = None
                record.last_success_at = record.last_validation_at
                record.health_status = "HEALTHY"
            else:
                record.validation_error = "Credential validation failed against the target API"
                record.health_status = "UNHEALTHY"
                raise ValueError(record.validation_error)
        except Exception as e:
            record.last_validation_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            record.validation_error = str(e)
            record.health_status = "UNHEALTHY"
            # We still save the record to the database, but raise an exception to the caller.
            self.repo.save_connector(tenant_id, record)
            raise ValueError(f"Failed to create connector due to validation error: {str(e)}") from e

        self.repo.save_connector(tenant_id, record)
        return record

    def update_connector(
        self,
        tenant_id: str,
        connector_id: str,
        name: Optional[str] = None,
        configuration: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        rate_limit_per_minute: Optional[int] = None,
        enabled: Optional[bool] = None
    ) -> ConnectorRecord:
        """Create a new version of the connector configuration (immutable history pattern) and validate it."""
        latest = self.repo.get_connector(tenant_id, connector_id)
        if not latest:
            raise ValueError(f"Connector '{connector_id}' not found in tenant '{tenant_id}'")

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        new_version = latest.connector_version + 1

        updated_record = ConnectorRecord(
            connector_id=connector_id,
            tenant_id=tenant_id,
            connector_type=latest.connector_type,
            name=name if name is not None else latest.name,
            description=description if description is not None else latest.description,
            enabled=enabled if enabled is not None else latest.enabled,
            configuration=configuration if configuration is not None else latest.configuration,
            connector_version=new_version,
            schema_version=latest.schema_version,
            health_status=latest.health_status,
            last_health_check=latest.last_health_check,
            last_success_at=latest.last_success_at,
            consecutive_failures=latest.consecutive_failures,
            last_validation_at=latest.last_validation_at,
            validation_error=latest.validation_error,
            rate_limit_per_minute=rate_limit_per_minute if rate_limit_per_minute is not None else latest.rate_limit_per_minute,
            circuit_state=latest.circuit_state,
            circuit_failure_count=latest.circuit_failure_count,
            circuit_opened_at=latest.circuit_opened_at,
            created_at=latest.created_at,
            updated_at=now_str
        )

        # Validate credentials if configuration is modified
        if configuration is not None or enabled is True:
            try:
                instance = self._get_connector_instance(tenant_id, updated_record)
                valid = instance.validate_credentials()
                updated_record.last_validation_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                if valid:
                    updated_record.validation_error = None
                    updated_record.last_success_at = updated_record.last_validation_at
                    updated_record.health_status = "HEALTHY"
                else:
                    updated_record.validation_error = "Credential validation failed against the target API"
                    updated_record.health_status = "UNHEALTHY"
                    raise ValueError(updated_record.validation_error)
            except Exception as e:
                updated_record.last_validation_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                updated_record.validation_error = str(e)
                updated_record.health_status = "UNHEALTHY"
                self.repo.save_connector(tenant_id, updated_record)
                raise ValueError(f"Failed to update connector due to validation error: {str(e)}") from e

        self.repo.save_connector(tenant_id, updated_record)
        return updated_record

    def get_connector(self, tenant_id: str, connector_id: str, version: Optional[int] = None) -> Optional[ConnectorRecord]:
        """Fetch a specific version of a connector or the latest version."""
        return self.repo.get_connector(tenant_id, connector_id, version)

    def delete_connector(self, tenant_id: str, connector_id: str) -> bool:
        """Delete all versions of a connector configuration."""
        return self.repo.delete_connector(tenant_id, connector_id)

    def list_connectors(self, tenant_id: str) -> List[ConnectorRecord]:
        """List the latest version of all connectors for a tenant."""
        return self.repo.list_connectors(tenant_id)

    def test_connector(self, tenant_id: str, connector_id: str) -> Dict[str, Any]:
        """Run an on-demand sandbox test (credential validation) against the target API."""
        record = self.repo.get_connector(tenant_id, connector_id)
        if not record:
            raise ValueError(f"Connector '{connector_id}' not found in tenant '{tenant_id}'")

        try:
            instance = self._get_connector_instance(tenant_id, record)
            valid = instance.validate_credentials()
            
            record.last_validation_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if valid:
                record.validation_error = None
                record.last_success_at = record.last_validation_at
                record.health_status = "HEALTHY"
                self.repo.save_connector(tenant_id, record)
                return {"success": True, "error": None}
            else:
                record.validation_error = "Credential validation failed against target API"
                record.health_status = "UNHEALTHY"
                self.repo.save_connector(tenant_id, record)
                return {"success": False, "error": record.validation_error}
        except Exception as e:
            record.last_validation_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            record.validation_error = str(e)
            record.health_status = "UNHEALTHY"
            self.repo.save_connector(tenant_id, record)
            return {"success": False, "error": str(e)}
