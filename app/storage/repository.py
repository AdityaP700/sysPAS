from abc import ABC, abstractmethod
from typing import List, Optional
from app.storage.models import BundleRecord, CompilationRecord, TraceRecord


class StorageRepository(ABC):
    """Abstract interface defining required tenant-scoped storage CRUD operations."""

    @abstractmethod
    def save_bundle(self, tenant_id: str, record: BundleRecord) -> None:
        """Persist a new version of a skill bundle inside a tenant workspace."""
        pass

    @abstractmethod
    def get_bundle(self, tenant_id: str, bundle_id: str, version: Optional[int] = None) -> Optional[BundleRecord]:
        """Retrieve a specific bundle version within a tenant workspace."""
        pass

    @abstractmethod
    def list_bundles(self, tenant_id: str) -> List[BundleRecord]:
        """Retrieve the latest version of all unique bundles stored within a tenant workspace."""
        pass

    @abstractmethod
    def delete_bundle(self, tenant_id: str, bundle_id: str) -> bool:
        """Delete all versions of a specific bundle from a tenant workspace."""
        pass

    @abstractmethod
    def save_compilation(self, tenant_id: str, record: CompilationRecord) -> None:
        """Persist a compilation execution history record under a tenant workspace."""
        pass

    @abstractmethod
    def get_compilation(self, tenant_id: str, compilation_id: str) -> Optional[CompilationRecord]:
        """Retrieve a single compilation record by its UUID within a tenant workspace."""
        pass

    @abstractmethod
    def list_compilations(self, tenant_id: str, bundle_id: str) -> List[CompilationRecord]:
        """List historical compilation records associated with a bundle ID within a tenant workspace."""
        pass

    @abstractmethod
    def save_trace(self, tenant_id: str, record: TraceRecord) -> None:
        """Persist a compilation trace step under a tenant workspace."""
        pass

    @abstractmethod
    def get_traces_by_compilation(self, tenant_id: str, compilation_id: str) -> List[TraceRecord]:
        """Retrieve all step traces compiled during a single execution run within a tenant workspace."""
        pass

    @abstractmethod
    def get_versions(self, tenant_id: str, bundle_id: str) -> List[BundleRecord]:
        """Retrieve all version records stored for a specific bundle ID within a tenant workspace."""
        pass
