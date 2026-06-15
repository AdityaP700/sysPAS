from typing import List, Optional
from app.storage.models import CompilationRecord
from app.storage.repository import StorageRepository


class CompilationStore:
    """Service layer managing compilation telemetry history and metrics persistence."""

    def __init__(self, repository: StorageRepository):
        self.repo = repository

    def save_compilation(self, record: CompilationRecord, tenant_id: str = "system") -> None:
        """Persist a new compilation execution record."""
        # Ensure the record has the tenant_id set
        if record.tenant_id == "system" and tenant_id != "system":
            record.tenant_id = tenant_id
        self.repo.save_compilation(tenant_id, record)

    def get_compilation(self, compilation_id: str, tenant_id: str = "system") -> Optional[CompilationRecord]:
        """Retrieve a specific compilation run by its ID."""
        return self.repo.get_compilation(tenant_id, compilation_id)

    def list_compilations(self, bundle_id: str, tenant_id: str = "system") -> List[CompilationRecord]:
        """List all historical compilation events recorded for a given bundle ID."""
        return self.repo.list_compilations(tenant_id, bundle_id)
