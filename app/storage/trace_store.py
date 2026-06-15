import uuid
from typing import List
from app.tracing.models import CompilationTrace
from app.storage.models import TraceRecord
from app.storage.repository import StorageRepository


class TraceStore:
    """Service layer persisting and converting step-level compiler CompilationTrace objects."""

    def __init__(self, repository: StorageRepository):
        self.repo = repository

    def save_traces(self, compilation_id: str, traces: List[CompilationTrace], tenant_id: str = "system") -> None:
        """Persist a list of compilation traces for a given compilation run."""
        for trace in traces:
            trace_id = str(uuid.uuid4())
            
            # Propagate tenant_id to CompilationTrace if needed
            if hasattr(trace, "tenant_id"):
                trace.tenant_id = tenant_id

            record = TraceRecord(
                trace_id=trace_id,
                compilation_id=compilation_id,
                step_id=trace.step_id,
                request_id=trace.request_id,
                correlation_id=trace.correlation_id,
                payload=trace.model_dump(),
                tenant_id=tenant_id,
            )
            self.repo.save_trace(tenant_id, record)

    def get_traces_by_compilation(self, compilation_id: str, tenant_id: str = "system") -> List[CompilationTrace]:
        """Retrieve and reconstruct CompilationTrace models associated with a compilation ID."""
        records = self.repo.get_traces_by_compilation(tenant_id, compilation_id)
        results = []
        for r in records:
            results.append(CompilationTrace(**r.payload))
        return results
