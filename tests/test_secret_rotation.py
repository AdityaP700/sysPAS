import pytest
import os
import tempfile
from app.storage.sqlite import SQLiteRepository
from app.vault.service import VaultService
from app.vault.models import SecretType
from app.config.settings import settings


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_secret_rotation_versions_and_cache(temp_db):
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        repo = SQLiteRepository(temp_db)
        service = VaultService(repo)
        
        tenant_id = "tenant-rotate"
        secret_name = "api_token"
        
        # 1. Create first version
        v1_rec = service.create_secret(tenant_id, secret_name, SecretType.TOKEN, "v1-token-val")
        assert v1_rec.version == 1
        assert v1_rec.is_current is True
        
        # Resolve to cache it
        resolved_v1 = service.resolve_secret(tenant_id, secret_name)
        assert resolved_v1 == "v1-token-val"
        
        # Check cache contains the value
        assert service.cache.get(tenant_id, secret_name, None) == "v1-token-val"
        
        # 2. Rotate to version 2
        v2_rec = service.rotate_secret(tenant_id, v1_rec.secret_id, "v2-token-val")
        assert v2_rec.version == 2
        assert v2_rec.is_current is True
        
        # Verify cache was invalidated for all versions (should return None for cache query)
        assert service.cache.get(tenant_id, secret_name, None) is None
        
        # Check version 1 record is still enabled but is_current is False in database
        db_v1 = repo.get_secret(tenant_id, v1_rec.secret_id)
        assert db_v1.enabled is True
        assert db_v1.is_current is False
        
        # 3. We should be able to resolve both the current and the older version explicitly
        assert service.resolve_secret(tenant_id, secret_name) == "v2-token-val" # Current (latest)
        assert service.resolve_secret(tenant_id, secret_name, version=1) == "v1-token-val" # Old version
    finally:
        settings.vault_master_key = old_key
