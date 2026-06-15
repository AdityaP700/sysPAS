import os
import tempfile
import pytest
from app.tracing.models import CompilationTrace
from app.storage.sqlite import SQLiteRepository
from app.storage.trace_store import TraceStore


@pytest.fixture
def temp_db_file():
    """Create a temporary database file and clean it up after the test completes."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_trace_store_save_retrieve(temp_db_file):
    """Test saving a list of CompilationTraces and retrieving/reconstructing them."""
    repo = SQLiteRepository(temp_db_file)
    store = TraceStore(repo)

    traces = [
        CompilationTrace(
            step_id="step_1",
            generated_spl="index=auth | stats count",
            optimized_spl="| tstats count WHERE index=auth",
            execution_duration_ms=45.2,
            request_id="req-123",
            correlation_id="corr-456",
            overall_confidence=0.9,
        ),
        CompilationTrace(
            step_id="step_2",
            generated_spl="index=firewall | stats count",
            optimized_spl="| tstats count WHERE index=firewall",
            execution_duration_ms=50.5,
            request_id="req-123",
            correlation_id="corr-456",
            overall_confidence=0.85,
        ),
    ]

    # Save
    store.save_traces("comp-uuid-1", traces)

    # Retrieve and verify reconstruction
    retrieved = store.get_traces_by_compilation("comp-uuid-1")
    assert len(retrieved) == 2
    assert retrieved[0].step_id == "step_1"
    assert retrieved[0].optimized_spl == "| tstats count WHERE index=auth"
    assert retrieved[0].request_id == "req-123"
    assert retrieved[0].correlation_id == "corr-456"
    assert retrieved[0].overall_confidence == 0.9

    assert retrieved[1].step_id == "step_2"
    assert retrieved[1].execution_duration_ms == 50.5
    assert retrieved[1].overall_confidence == 0.85

    # Retrieve non-existent
    assert len(store.get_traces_by_compilation("non-existent")) == 0
