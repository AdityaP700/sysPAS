from abc import ABC, abstractmethod
from typing import List, Optional
from app.auth.models import APIKeyRecord, TenantRecord, MembershipRecord


class APIKeyRepository(ABC):
    """Abstract interface defining operations for managing API keys, Tenants, and memberships."""

    @abstractmethod
    def save_api_key(self, tenant_id: str, record: APIKeyRecord) -> None:
        """Persist a new API key record inside a tenant workspace."""
        pass

    @abstractmethod
    def get_api_key_by_hash(self, key_hash: str) -> Optional[APIKeyRecord]:
        """Look up an API key by its SHA-256 hash representation globally."""
        pass

    @abstractmethod
    def get_api_key_by_id(self, tenant_id: str, key_id: str) -> Optional[APIKeyRecord]:
        """Look up an API key by its unique identifier within a tenant workspace."""
        pass

    @abstractmethod
    def list_api_keys(self, tenant_id: str) -> List[APIKeyRecord]:
        """List metadata summaries of all keys in a tenant workspace."""
        pass

    @abstractmethod
    def revoke_api_key(self, tenant_id: str, key_id: str) -> bool:
        """Disable or revoke an API key by ID within a tenant workspace."""
        pass

    # --- Tenant Management Interfaces ---

    @abstractmethod
    def save_tenant(self, record: TenantRecord) -> None:
        """Persist or edit a tenant workspace organization record."""
        pass

    @abstractmethod
    def get_tenant(self, tenant_id: str) -> Optional[TenantRecord]:
        """Retrieve an active, non-deleted tenant record."""
        pass

    @abstractmethod
    def list_tenants(self) -> List[TenantRecord]:
        """List all active, non-deleted tenants."""
        pass

    @abstractmethod
    def delete_tenant(self, tenant_id: str) -> bool:
        """Soft-delete a tenant workspace, disabling it and setting deleted_at."""
        pass

    # --- Membership Management Interfaces ---

    @abstractmethod
    def save_membership(self, record: MembershipRecord) -> None:
        """Persist or update a membership mapping associate."""
        pass

    @abstractmethod
    def get_memberships(self, tenant_id: str) -> List[MembershipRecord]:
        """List all memberships belonging to a tenant workspace."""
        pass

    @abstractmethod
    def delete_membership(self, tenant_id: str, membership_id: str) -> bool:
        """Remove a membership mapping from the tenant workspace."""
        pass

