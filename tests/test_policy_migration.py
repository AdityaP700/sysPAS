import pytest
import os
import sqlite3
import tempfile
from app.storage.sqlite import SQLiteRepository


@pytest.fixture
def legacy_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Setup legacy tables directly using sqlite3 (pre-Phase 22 schema)
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    
    # Legacy bundles table (no environment / promotion_status)
    cursor.execute(
        """
        CREATE TABLE bundles (
            bundle_id TEXT,
            bundle_name TEXT,
            version INTEGER,
            created_at TEXT,
            status TEXT,
            payload TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'system',
            created_by TEXT,
            PRIMARY KEY (bundle_id, version)
        )
        """
    )
    cursor.execute(
        "INSERT INTO bundles (bundle_id, bundle_name, version, created_at, status, payload, tenant_id) "
        "VALUES ('b-legacy', 'Legacy SOP', 1, '2026-06-13T12:00:00Z', 'COMPILED', '{}', 'tenant-1')"
    )

    # Legacy secrets table (no environment)
    cursor.execute(
        """
        CREATE TABLE secrets (
            secret_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            secret_type TEXT,
            encrypted_value TEXT NOT NULL,
            version INTEGER,
            enabled INTEGER,
            is_current INTEGER,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cursor.execute(
        "INSERT INTO secrets (secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at) "
        "VALUES ('s-legacy', 'tenant-1', 'api_key', 'API_KEY', 'encval', 1, 1, 1, '2026-06-13T12:00:00Z', '2026-06-13T12:00:00Z')"
    )

    # Legacy connectors table (no environment)
    cursor.execute(
        """
        CREATE TABLE connectors (
            connector_id TEXT,
            tenant_id TEXT NOT NULL,
            connector_type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            enabled INTEGER DEFAULT 1,
            configuration TEXT NOT NULL,
            connector_version INTEGER NOT NULL DEFAULT 1,
            schema_version INTEGER NOT NULL DEFAULT 1,
            health_status TEXT DEFAULT 'UNKNOWN',
            last_health_check TEXT,
            last_success_at TEXT,
            consecutive_failures INTEGER DEFAULT 0,
            last_validation_at TEXT,
            validation_error TEXT,
            rate_limit_per_minute INTEGER DEFAULT 100,
            circuit_state TEXT DEFAULT 'CLOSED',
            circuit_failure_count INTEGER DEFAULT 0,
            circuit_opened_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (connector_id, connector_version)
        )
        """
    )
    cursor.execute(
        "INSERT INTO connectors (connector_id, tenant_id, connector_type, name, configuration, created_at, updated_at) "
        "VALUES ('c-legacy', 'tenant-1', 'SLACK', 'Slack Connector', '{}', '2026-06-13T12:00:00Z', '2026-06-13T12:00:00Z')"
    )

    conn.commit()
    conn.close()

    yield path

    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_schema_migration_adds_environment_columns(legacy_db):
    # Instantiate SQLiteRepository which automatically triggers table creation and migrations
    repo = SQLiteRepository(legacy_db)

    # Verify that the legacy bundle record now has environment='DEV' and promotion_status='DRAFT'
    bundle = repo.get_bundle("tenant-1", "b-legacy", 1)
    assert bundle is not None
    assert bundle.environment == "DEV"
    assert bundle.promotion_status == "DRAFT"

    # Verify secret has environment='DEV'
    secret = repo.get_secret("tenant-1", "s-legacy")
    assert secret is not None
    assert secret.environment == "DEV"

    # Verify connector has environment='DEV'
    connector = repo.get_connector("tenant-1", "c-legacy", 1)
    assert connector is not None
    assert connector.environment == "DEV"
