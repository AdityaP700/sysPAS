import time
import pytest
from app.schema.cache import SchemaCache


def test_cache_set_and_get():
    """Verify standard set/get operations with cache hit."""
    cache = SchemaCache()
    fields = ["src_ip", "user", "action"]
    
    # Verify miss
    assert cache.get("auth_logs") is None
    
    # Set and verify hit
    cache.set("auth_logs", fields, ttl=10.0)
    assert cache.get("auth_logs") == fields


def test_cache_ttl_expiration():
    """Verify that cached values expire after their TTL duration."""
    cache = SchemaCache()
    fields = ["src_ip"]
    
    # Set with a very short TTL
    cache.set("auth_logs", fields, ttl=0.01)
    
    # Immediate lookup succeeds
    assert cache.get("auth_logs") == fields
    
    # Wait for expiration
    time.sleep(0.02)
    assert cache.get("auth_logs") is None


def test_cache_invalidation():
    """Verify manual invalidation of specific entries."""
    cache = SchemaCache()
    fields = ["src_ip"]
    
    cache.set("auth_logs", fields, ttl=10.0)
    assert cache.get("auth_logs") == fields
    
    cache.invalidate("auth_logs")
    assert cache.get("auth_logs") is None


def test_cache_clear():
    """Verify clearing the entire cache."""
    cache = SchemaCache()
    cache.set("index1", ["field1"], ttl=10.0)
    cache.set("index2", ["field2"], ttl=10.0)
    
    assert cache.get("index1") == ["field1"]
    assert cache.get("index2") == ["field2"]
    
    cache.clear()
    
    assert cache.get("index1") is None
    assert cache.get("index2") is None
