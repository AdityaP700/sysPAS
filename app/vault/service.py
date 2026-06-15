import re
import time
import uuid
import threading
from datetime import datetime, timezone
from typing import List, Optional, Dict

from app.vault.models import SecretRecord, SecretType
from app.vault.crypto import EncryptionService
from app.vault.cache import SecretCache
from app.storage.sqlite import SQLiteRepository


class VaultRateLimitExceeded(Exception):
    """Raised when a tenant exceeds the allowed number of secret resolutions per minute."""
    pass


class VaultService:
    """Service layer orchestrating tenant-scoped vault operations, caching, encryption, and rate limiting."""

    def __init__(self, repo: SQLiteRepository):
        self.repo = repo
        self.crypto = EncryptionService()
        self.cache = SecretCache()
        self._lock = threading.Lock()
        # Sliding window timestamps: tenant_id -> list of float timestamps
        self._resolutions: Dict[str, List[float]] = {}
        # Decision cache: (tenant_id, name) -> (timestamp, allowed, violations)
        self._decision_cache: Dict[tuple, tuple] = {}

    def _check_rate_limit(self, tenant_id: str) -> None:
        """Enforces sliding-window rate limits of 100 resolutions per minute per tenant."""
        now = time.time()
        cutoff = now - 60.0
        with self._lock:
            timestamps = self._resolutions.get(tenant_id, [])
            # Filter out timestamps older than 60s
            timestamps = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= 100:
                raise VaultRateLimitExceeded(
                    f"Rate limit exceeded: Tenant '{tenant_id}' reached the limit of 100 resolutions/min."
                )
            timestamps.append(now)
            self._resolutions[tenant_id] = timestamps

    def create_secret(self, tenant_id: str, name: str, secret_type: SecretType, plaintext: str, environment: str = "DEV") -> SecretRecord:
        """Creates a new version 1 secret in the tenant vault."""
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise ValueError(f"Invalid secret name format '{name}': only alphanumeric, '_' and '-' are allowed.")
        if not plaintext:
            raise ValueError("Secret value cannot be empty.")

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        encrypted_val = self.crypto.encrypt(plaintext)

        # Check if a secret with the same name already exists in this tenant (to avoid duplicate base name creation)
        existing = self.repo.get_secret_by_name(tenant_id, name)
        if existing:
            raise ValueError(f"Secret with name '{name}' already exists in tenant '{tenant_id}'. Use rotate to update it.")

        secret_id = f"sec_{uuid.uuid4().hex[:12]}"
        record = SecretRecord(
            secret_id=secret_id,
            tenant_id=tenant_id,
            name=name,
            secret_type=secret_type,
            encrypted_value=encrypted_val,
            version=1,
            enabled=True,
            is_current=True,
            created_at=now_str,
            updated_at=now_str,
            environment=environment
        )
        self.repo.save_secret(tenant_id, record)

        return record

    def _evaluate_secret_policy(self, tenant_id: str, record: SecretRecord) -> None:
        """Enforces PolicyType.SECRET check with 5s decision cache."""
        now = time.time()
        cache_key = (tenant_id, record.name)
        
        with self._lock:
            cached = self._decision_cache.get(cache_key)
            if cached:
                ts, allowed, violations = cached
                if now - ts < 5.0:
                    if not allowed:
                        raise ValueError(f"Access to secret '{record.name}' blocked by policy: {violations}")
                    return

        # Fetch environment context
        exec_env = "DEV"
        try:
            executions = self.repo.list_executions(tenant_id)
            running = [e for e in executions if e.status.value in ("RUNNING", "PENDING")]
            if running:
                latest_exec = running[0]
                bundle_rec = self.repo.get_bundle(tenant_id, latest_exec.bundle_id, latest_exec.bundle_version)
                if bundle_rec:
                    exec_env = bundle_rec.environment
        except Exception:
            pass

        from app.governance.policy_engine import PolicyEngine
        from app.governance.models import PolicyType
        engine = PolicyEngine(self.repo)
        
        context = {
            "resource_id": record.name,
            "secret_name": record.name,
            "secret_environment": record.environment,
            "environment": exec_env,
        }
        
        decision = engine.evaluate(tenant_id, PolicyType.SECRET, context)
        
        with self._lock:
            self._decision_cache[cache_key] = (now, decision.allowed, decision.violations)
            
        if not decision.allowed:
            raise ValueError(f"Access to secret '{record.name}' blocked by policy: {decision.violations[0] if decision.violations else 'Access denied'}")

    def resolve_secret(self, tenant_id: str, name: str, version: Optional[int] = None) -> str:
        """Resolves a decrypted secret value, checking cache and sliding-window rate limit first."""
        self._check_rate_limit(tenant_id)

        # 1. Fetch secret metadata first to evaluate policy
        record = self.repo.get_secret_by_name(tenant_id, name, version)
        if not record or not record.enabled:
            ver_str = f" version {version}" if version else ""
            raise ValueError(f"Secret '{name}'{ver_str} not found or is disabled in tenant '{tenant_id}'.")

        # Evaluate policy check (with 5s decision caching)
        self._evaluate_secret_policy(tenant_id, record)

        # 2. Check Cache for decrypted value
        cached_val = self.cache.get(tenant_id, name, version)
        if cached_val is not None:
            return cached_val

        # 3. Decrypt
        decrypted = self.crypto.decrypt(record.encrypted_value)

        # 4. Save to Cache
        from app.config.settings import settings
        ttl = settings.secret_cache_ttl_seconds
        self.cache.set(tenant_id, name, version, decrypted, ttl)

        return decrypted

    def rotate_secret(self, tenant_id: str, secret_id: str, new_plaintext: str, environment: Optional[str] = None) -> SecretRecord:
        """Rotates a secret value by saving a new version and setting it current, keeping rollback versions enabled."""
        if not new_plaintext:
            raise ValueError("Rotated secret value cannot be empty.")

        # 1. Fetch existing secret to extract name and type
        existing = self.repo.get_secret(tenant_id, secret_id)
        if not existing:
            raise ValueError(f"Secret '{secret_id}' not found in tenant '{tenant_id}'.")

        name = existing.name
        secret_type = existing.secret_type
        env = environment or existing.environment

        # 2. Query all versions of this secret in this tenant to determine the next version number
        secrets_list = self.repo.list_secrets(tenant_id)
        matching_versions = [s.version for s in secrets_list if s.name == name]
        next_version = max(matching_versions) + 1 if matching_versions else existing.version + 1

        # 3. Encrypt and save new version
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        encrypted_val = self.crypto.encrypt(new_plaintext)

        new_secret_id = f"sec_{uuid.uuid4().hex[:12]}"
        new_record = SecretRecord(
            secret_id=new_secret_id,
            tenant_id=tenant_id,
            name=name,
            secret_type=secret_type,
            encrypted_value=encrypted_val,
            version=next_version,
            enabled=True,
            is_current=True,
            created_at=now_str,
            updated_at=now_str,
            environment=env
        )

        self.repo.save_secret(tenant_id, new_record)

        # 5. Invalidate ALL versions from cache
        self.cache.invalidate(tenant_id, name, None)

        return new_record

    def disable_secret(self, tenant_id: str, secret_id: str) -> bool:
        """Disables the secret and invalidates its cache."""
        existing = self.repo.get_secret(tenant_id, secret_id)
        if not existing:
            return False

        disabled = self.repo.disable_secret(tenant_id, secret_id)
        if disabled:
            # Invalidate cache for this name
            self.cache.invalidate(tenant_id, existing.name, None)
            return True
        return False
