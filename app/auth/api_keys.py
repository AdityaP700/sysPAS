import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple
from app.auth.models import APIKeyRecord, UserRole, GlobalRole, TenantRole
from app.auth.repository import APIKeyRepository


class APIKeyManager:
    """Core logic wrapper responsible for key generation, SHA-256 hashing, and validation."""

    def __init__(self, repo: APIKeyRepository):
        self.repo = repo

    @staticmethod
    def generate_raw_token() -> str:
        """Create a cryptographically secure random token prefixed with rm_key_."""
        token = secrets.token_urlsafe(32)
        return f"rm_key_{token}"

    @staticmethod
    def compute_hash(token: str) -> str:
        """Calculate the SHA-256 representation of the token string."""
        return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()

    @staticmethod
    def extract_prefix(token: str) -> str:
        """
        Extract a revealable preview prefix for key listings.
        Example: 'rm_key_abcde123' -> 'rm_key_abcde'
        """
        # Ensure we don't return too much of the secret. We take up to 11 chars (rm_key_ + 4 chars).
        return token[:11]

    def create_api_key(
        self,
        name: str,
        role: Optional[UserRole] = None,
        tenant_id: str = "system",
        tenant_role: Optional[TenantRole] = None,
        global_role: Optional[GlobalRole] = None
    ) -> Tuple[str, APIKeyRecord]:
        """
        Generates and saves a new API key record.
        Returns a tuple of (plaintext_raw_token, saved_metadata_record).
        """
        raw_token = self.generate_raw_token()
        key_hash = self.compute_hash(raw_token)
        prefix = self.extract_prefix(raw_token)
        key_id = f"key_{secrets.token_hex(8)}"

        record = APIKeyRecord(
            key_id=key_id,
            name=name,
            key_hash=key_hash,
            key_prefix=prefix,
            tenant_id=tenant_id,
            global_role=global_role,
            tenant_role=tenant_role,
            role=role,
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            enabled=True,
        )
        self.repo.save_api_key(tenant_id, record)
        return raw_token, record

    def validate_api_key(self, token: str) -> Optional[APIKeyRecord]:
        """Check the raw token against stored hashed tokens. Returns APIKeyRecord if valid and enabled."""
        key_hash = self.compute_hash(token)
        record = self.repo.get_api_key_by_hash(key_hash)
        if record and record.enabled:
            return record
        return None
