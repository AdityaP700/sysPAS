import time
import pytest
from app.vault.cache import SecretCache


def test_secret_cache_ttl_and_expiry():
    cache = SecretCache()
    tenant_id = "tenant-cache"
    name = "my_secret"
    decrypted = "my-plain-text"
    
    # 1. Cache Miss
    assert cache.get(tenant_id, name, version=None) is None
    
    # 2. Cache Set and Get
    cache.set(tenant_id, name, version=None, decrypted=decrypted, ttl=1)
    assert cache.get(tenant_id, name, version=None) == decrypted
    
    # 3. Cache Expiry (wait 1.1 seconds)
    time.sleep(1.1)
    assert cache.get(tenant_id, name, version=None) is None


def test_secret_cache_invalidation():
    cache = SecretCache()
    tenant_id = "tenant-cache-2"
    name = "my_secret_2"
    
    cache.set(tenant_id, name, version=None, decrypted="current-val", ttl=60)
    cache.set(tenant_id, name, version=1, decrypted="v1-val", ttl=60)
    cache.set(tenant_id, name, version=2, decrypted="v2-val", ttl=60)
    
    # Get all from cache
    assert cache.get(tenant_id, name, version=None) == "current-val"
    assert cache.get(tenant_id, name, version=1) == "v1-val"
    assert cache.get(tenant_id, name, version=2) == "v2-val"
    
    # Invalidate version 1 specifically
    cache.invalidate(tenant_id, name, version=1)
    assert cache.get(tenant_id, name, version=1) is None
    assert cache.get(tenant_id, name, version=None) == "current-val"
    
    # Invalidate all versions (version=None parameter in invalidate)
    cache.invalidate(tenant_id, name, version=None)
    assert cache.get(tenant_id, name, version=None) is None
    assert cache.get(tenant_id, name, version=2) is None
