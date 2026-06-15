import uuid
from datetime import datetime, timezone
from typing import List, Optional
from app.package.bundle import SkillBundle
from app.storage.models import BundleRecord
from app.storage.repository import StorageRepository


class BundleStore:
    """Service layer managing SkillBundle persistence, unique UUID routing, and version increments."""

    def __init__(self, repository: StorageRepository):
        self.repo = repository

    def _get_or_create_bundle_id(self, bundle_name: str, tenant_id: str = "system") -> str:
        """Find an existing bundle ID for a given name, or generate a new random UUID."""
        # Clean the input name for robust comparison
        clean_name = bundle_name.strip().lower()
        for record in self.repo.list_bundles(tenant_id):
            if record.bundle_name.strip().lower() == clean_name:
                return record.bundle_id
        return str(uuid.uuid4())

    def save_bundle(
        self,
        bundle_name: str,
        skill_bundle: SkillBundle,
        status: str,
        created_by: str = "system",
        tenant_id: str = "system",
        owner_id: Optional[str] = None
    ) -> BundleRecord:
        """
        Saves a compiled SkillBundle artifact, incrementing the version based on name.
        Ensures existing versions are never overwritten.
        """
        creator = owner_id or created_by
        bundle_id = self._get_or_create_bundle_id(bundle_name, tenant_id)
        existing_versions = self.repo.get_versions(tenant_id, bundle_id)
        
        if existing_versions:
            new_version = max(v.version for v in existing_versions) + 1
        else:
            new_version = 1

        # Synchronize version number into the manifest
        skill_bundle.manifest.version = str(new_version)

        # Build records
        created_at = skill_bundle.manifest.created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record = BundleRecord(
            bundle_id=bundle_id,
            bundle_name=bundle_name,
            version=new_version,
            created_at=created_at,
            status=status,
            payload=skill_bundle.model_dump(),
            tenant_id=tenant_id,
            created_by=creator,
        )

        # Safeguard assertion
        for v in existing_versions:
            if v.version == new_version:
                raise ValueError(f"Version {new_version} of bundle '{bundle_name}' already exists.")

        self.repo.save_bundle(tenant_id, record)
        return record

    def get_bundle(self, bundle_id: str, version: Optional[int] = None, tenant_id: str = "system") -> Optional[BundleRecord]:
        """Retrieve a specific bundle version."""
        return self.repo.get_bundle(tenant_id, bundle_id, version)

    def list_bundles(self, tenant_id: str = "system") -> List[BundleRecord]:
        """List the latest version of all unique bundles."""
        return self.repo.list_bundles(tenant_id)

    def get_versions(self, bundle_id: str, tenant_id: str = "system") -> List[BundleRecord]:
        """List all version history records of a specific bundle."""
        return self.repo.get_versions(tenant_id, bundle_id)

    def delete_bundle(self, bundle_id: str, tenant_id: str = "system") -> bool:
        """Delete all version history for a bundle ID."""
        return self.repo.delete_bundle(tenant_id, bundle_id)
