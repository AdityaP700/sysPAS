import os
import tempfile
import pytest
from datetime import datetime, timezone
from app.storage.sqlite import SQLiteRepository
from app.auth.models import TenantRecord, MembershipRecord, TenantRole


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


def test_memberships_management(temp_db_file):
    repo = SQLiteRepository(temp_db_file)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Setup: Create tenant
    t1 = TenantRecord(
        tenant_id="tenant-1",
        name="SOC Operations",
        slug="soc-team",
        created_at=now,
        enabled=True,
        deleted_at=None
    )
    repo.save_tenant(t1)

    # 1. Map API key to tenant-scoped role
    m1 = MembershipRecord(
        membership_id="m-1",
        tenant_id="tenant-1",
        api_key_id="key-operator-1",
        role=TenantRole.TENANT_OPERATOR
    )
    repo.save_membership(m1)

    # 2. Verify membership listing
    memberships = repo.get_memberships("tenant-1")
    assert len(memberships) == 1
    assert memberships[0].api_key_id == "key-operator-1"
    assert memberships[0].role == TenantRole.TENANT_OPERATOR

    # 3. Add second membership
    m2 = MembershipRecord(
        membership_id="m-2",
        tenant_id="tenant-1",
        api_key_id="key-viewer-1",
        role=TenantRole.TENANT_VIEWER
    )
    repo.save_membership(m2)
    assert len(repo.get_memberships("tenant-1")) == 2

    # 4. Enforce unique constraint: same key cannot get multiple memberships in same tenant
    m_dup = MembershipRecord(
        membership_id="m-dup",
        tenant_id="tenant-1",
        api_key_id="key-operator-1",  # Duplicate API key!
        role=TenantRole.TENANT_ADMIN
    )
    with pytest.raises(Exception) as exc_info:
        repo.save_membership(m_dup)
    assert "UNIQUE constraint failed" in str(exc_info.value)

    # 5. Delete membership
    deleted = repo.delete_membership("tenant-1", "m-1")
    assert deleted is True
    assert len(repo.get_memberships("tenant-1")) == 1
    assert repo.get_memberships("tenant-1")[0].api_key_id == "key-viewer-1"
