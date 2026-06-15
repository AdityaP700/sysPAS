import threading
import time
from typing import Dict, Any, Optional, Tuple


class SecretCache:
    """Thread-safe in-memory cache with TTL expiration for decrypted secret values."""

    def __init__(self):
        self._lock = threading.Lock()
        # Storage schema: (tenant_id, name, version) -> (decrypted_value, expire_at)
        # where version can be an integer or None (for current/latest)
        self._cache: Dict[Tuple[str, str, Optional[int]], Tuple[str, float]] = {}

    def get(self, tenant_id: str, name: str, version: Optional[int]) -> Optional[str]:
        """Fetches value if present and not expired, otherwise returns None."""
        key = (tenant_id, name, version)
        with self._lock:
            if key not in self._cache:
                return None
            val, expire_at = self._cache[key]
            if time.time() > expire_at:
                del self._cache[key]
                return None
            return val

    def set(self, tenant_id: str, name: str, version: Optional[int], decrypted: str, ttl: int) -> None:
        """Saves decrypted value in the cache with the given TTL in seconds."""
        key = (tenant_id, name, version)
        expire_at = time.time() + ttl
        with self._lock:
            self._cache[key] = (decrypted, expire_at)

    def invalidate(self, tenant_id: str, name: str, version: Optional[int] = None) -> None:
        """
        Invalidates cached entries.
        If version is None, invalidates ALL versions associated with the name (including version=None).
        Otherwise invalidates only the specific version key.
        """
        with self._lock:
            if version is None:
                # Find and delete all versions for the given (tenant_id, name)
                keys_to_del = [k for k in self._cache.keys() if k[0] == tenant_id and k[1] == name]
                for k in keys_to_del:
                    self._cache.pop(k, None)
            else:
                self._cache.pop((tenant_id, name, version), None)

    def clear(self) -> None:
        """Clears all elements in the cache."""
        with self._lock:
            self._cache.clear()
