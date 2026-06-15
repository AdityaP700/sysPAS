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


def test_vault_tenant_isolation(temp_db):
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        repo = SQLiteRepository(temp_db)
        service = VaultService(repo)
        
        # 1. Create secret with the SAME name in two different tenants
        name = "shared_key"
        service.create_secret("tenant-A", name, SecretType.GENERIC, "secret-A-value")
        service.create_secret("tenant-B", name, SecretType.GENERIC, "secret-B-value")
        
        # 2. Resolve them under their respective tenants
        assert service.resolve_secret("tenant-A", name) == "secret-A-value"
        assert service.resolve_secret("tenant-B", name) == "secret-B-value"
        
        # 3. Try to resolve tenant-A's secret under tenant-C (should fail)
        with pytest.raises(ValueError):
            service.resolve_secret("tenant-C", name)
            
        # 4. Verify lists are isolated
        list_A = service.repo.list_secrets("tenant-A")
        list_B = service.repo.list_secrets("tenant-B")
        list_C = service.repo.list_secrets("tenant-C")
        
        assert len(list_A) == 1
        assert len(list_B) == 1
        assert len(list_C) == 0
        
    finally:
        settings.vault_master_key = old_key
