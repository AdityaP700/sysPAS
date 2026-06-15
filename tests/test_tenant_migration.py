import os
import sqlite3
import tempfile
import pytest
from app.storage.sqlite import SQLiteRepository


@pytest.fixture
def temp_db_file():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_legacy_database_schema_migration(temp_db_file):
    # 1. Create a legacy SQLite schema manually (Phase 14 style, no tenant_id columns)
    conn = sqlite3.connect(temp_db_file)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE bundles (
            bundle_id TEXT,
            bundle_name TEXT,
            version INTEGER,
            created_at TEXT,
            status TEXT,
            payload TEXT,
            owner_id TEXT, -- Legacy column
            PRIMARY KEY (bundle_id, version)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE compilations (
            compilation_id TEXT PRIMARY KEY,
            bundle_id TEXT,
            timestamp TEXT,
            duration_ms REAL,
            confidence REAL,
            status TEXT
        )
        """
    )
    # Insert legacy records
    cursor.execute(
        "INSERT INTO bundles VALUES ('b-1', 'Legacy Runbook', 1, '2026-06-12T00:00:00Z', 'SUCCESS', '{}', 'legacy_user')"
    )
    conn.commit()
    conn.close()

    # 2. Instantiate SQLiteRepository against the legacy file
    # This should trigger _create_tables() -> _migrate_schema() -> _create_indexes() -> _bootstrap_system_tenant()
    repo = SQLiteRepository(temp_db_file)

    # 3. Verify that the tables have been migrated
    conn = sqlite3.connect(temp_db_file)
    cursor = conn.cursor()
    
    # Verify tenant_id column added to bundles
    cursor.execute("PRAGMA table_info(bundles);")
    cols = [row[1] for row in cursor.fetchall()]
    assert "tenant_id" in cols
    assert "created_by" in cols

    # Verify tenant_id column added to compilations
    cursor.execute("PRAGMA table_info(compilations);")
    comp_cols = [row[1] for row in cursor.fetchall()]
    assert "tenant_id" in comp_cols

    # Verify legacy record was mapped to "system" tenant and created_by mapped from owner_id
    cursor.execute("SELECT tenant_id, created_by FROM bundles WHERE bundle_id = 'b-1'")
    row = cursor.fetchone()
    assert row[0] == "system"
    assert row[1] == "legacy_user"

    # Verify system tenant is registered
    cursor.execute("SELECT tenant_id, name, slug FROM tenants WHERE tenant_id = 'system'")
    tenant_row = cursor.fetchone()
    assert tenant_row is not None
    assert tenant_row[1] == "System Tenant"
    assert tenant_row[2] == "system"

    # Verify composite indexes exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    indexes = [row[0] for row in cursor.fetchall()]
    assert "idx_bundles_tenant_bundle" in indexes
    assert "idx_compilations_tenant_compilation" in indexes

    conn.close()
