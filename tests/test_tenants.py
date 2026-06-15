import os
import tempfile
import pytest
from datetime import datetime, timezone
from app.storage.sqlite import SQLiteRepository
from app.auth.models import TenantRecord


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


def test_tenant_creation_and_soft_delete(temp_db_file):
    repo = SQLiteRepository(temp_db_file)
    
    # 1. Create a tenant
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    t1 = TenantRecord(
        tenant_id="tenant-1",
        name="SOC Operations",
        slug="soc-team",
        created_at=now,
        enabled=True,
        deleted_at=None
    )
    repo.save_tenant(t1)
    
    # 2. Get active tenant (should succeed)
    retrieved = repo.get_tenant("tenant-1")
    assert retrieved is not None
    assert retrieved.name == "SOC Operations"
    assert retrieved.slug == "soc-team"
    assert retrieved.enabled is True
    assert retrieved.deleted_at is None

    # 3. Soft delete the tenant
    deleted = repo.delete_tenant("tenant-1")
    assert deleted is True

    # 4. Get active tenant (should return None because it is soft-deleted)
    retrieved_after_delete = repo.get_tenant("tenant-1")
    assert retrieved_after_delete is None

    # 5. Retrieve directly from sqlite db using connection to see the soft-deleted state
    import sqlite3
    conn = sqlite3.connect(temp_db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT enabled, deleted_at FROM tenants WHERE tenant_id = 'tenant-1'")
    row = cursor.fetchone()
    conn.close()
    
    assert row is not None
    assert row[0] == 0  # enabled = 0
    assert row[1] is not None  # deleted_at timestamp is set


def test_tenant_slug_uniqueness(temp_db_file):
    repo = SQLiteRepository(temp_db_file)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Create tenant 1
    t1 = TenantRecord(
        tenant_id="tenant-1",
        name="SOC Operations",
        slug="soc-team",
        created_at=now,
        enabled=True,
        deleted_at=None
    )
    repo.save_tenant(t1)

    # Create tenant 2 with duplicate slug (should fail)
    t2 = TenantRecord(
        tenant_id="tenant-2",
        name="Security Team",
        slug="soc-team",  # Duplicate slug!
        created_at=now,
        enabled=True,
        deleted_at=None
    )
    
    with pytest.raises(Exception) as exc_info:
        repo.save_tenant(t2)
    assert "UNIQUE constraint failed" in str(exc_info.value)
