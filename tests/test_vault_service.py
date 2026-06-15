import pytest
import os
import tempfile
import time
from app.storage.sqlite import SQLiteRepository
from app.vault.service import VaultService, VaultRateLimitExceeded
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


def test_vault_service_crud(temp_db):
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        repo = SQLiteRepository(temp_db)
        service = VaultService(repo)
        
        tenant_id = "tenant-1"
        secret_name = "db_password"
        secret_value = "super-safe-pass"
        
        # 1. Create secret
        record = service.create_secret(tenant_id, secret_name, SecretType.PASSWORD, secret_value)
        assert record.name == secret_name
        assert record.version == 1
        assert record.enabled is True
        assert record.is_current is True
        
        # 2. Resolve secret
        resolved = service.resolve_secret(tenant_id, secret_name)
        assert resolved == secret_value
        
        # 3. Create duplicate name should fail
        with pytest.raises(ValueError) as exc:
            service.create_secret(tenant_id, secret_name, SecretType.PASSWORD, "another")
        assert "already exists" in str(exc.value)
        
        # 4. Resolve non-existent should fail
        with pytest.raises(ValueError):
            service.resolve_secret(tenant_id, "non_existent")
    finally:
        settings.vault_master_key = old_key


def test_vault_service_rate_limiting(temp_db):
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        repo = SQLiteRepository(temp_db)
        service = VaultService(repo)
        
        tenant_id = "tenant-limit"
        secret_name = "limit_key"
        service.create_secret(tenant_id, secret_name, SecretType.API_KEY, "test-api-val")
        
        # We trigger 100 resolutions. The 101st should fail with VaultRateLimitExceeded.
        for _ in range(100):
            service.resolve_secret(tenant_id, secret_name)
            
        with pytest.raises(VaultRateLimitExceeded):
            service.resolve_secret(tenant_id, secret_name)
    finally:
        settings.vault_master_key = old_key
