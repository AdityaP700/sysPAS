import time
import threading
from typing import Dict, List, Optional, Tuple
from app.config.settings import settings


class SchemaCache:
    """Thread-safe cache for keeping Splunk schema index-to-fields metadata."""

    def __init__(self):
        self._lock = threading.Lock()
        # Storage format: {index_name: (expiry_timestamp, list_of_fields)}
        self._cache: Dict[str, Tuple[float, List[str]]] = {}

    def get(self, key: str) -> Optional[List[str]]:
        """Retrieve cached fields for an index if they are not expired."""
        with self._lock:
            if key not in self._cache:
                return None
            
            expiry, fields = self._cache[key]
            if time.time() > expiry:
                # Cache entry expired, remove it
                del self._cache[key]
                return None
                
            return fields

    def set(self, key: str, fields: List[str], ttl: Optional[float] = None):
        """Cache fields list for an index with a specified or default TTL duration."""
        cache_ttl = ttl if ttl is not None else settings.schema_cache_ttl
        expiry = time.time() + cache_ttl
        with self._lock:
            self._cache[key] = (expiry, list(fields))

    def invalidate(self, key: str):
        """Invalidate/remove a specific index cache entry."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]

    def clear(self):
        """Clear all entries in the cache."""
        with self._lock:
            self._cache.clear()
