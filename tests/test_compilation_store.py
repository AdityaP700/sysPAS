import os
import tempfile
import pytest
from app.storage.sqlite import SQLiteRepository
from app.storage.compilation_store import CompilationStore
from app.storage.models import CompilationRecord


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


def test_compilation_store_save_retrieve_list(temp_db_file):
    """Test saving compilation records, retrieving by ID, and listing history by bundle ID."""
    repo = SQLiteRepository(temp_db_file)
    store = CompilationStore(repo)

    c1 = CompilationRecord(
        compilation_id="comp-uuid-1",
        bundle_id="bundle-uuid-abc",
        timestamp="2026-06-12T00:00:00Z",
        duration_ms=150.0,
        confidence=0.8,
        status="PARTIAL",
    )
    c2 = CompilationRecord(
        compilation_id="comp-uuid-2",
        bundle_id="bundle-uuid-abc",
        timestamp="2026-06-12T00:05:00Z",
        duration_ms=180.5,
        confidence=0.95,
        status="SUCCESS",
    )
    c3 = CompilationRecord(
        compilation_id="comp-uuid-3",
        bundle_id="bundle-uuid-other",
        timestamp="2026-06-12T00:10:00Z",
        duration_ms=90.0,
        confidence=0.7,
        status="FAILED",
    )

    store.save_compilation(c1)
    store.save_compilation(c2)
    store.save_compilation(c3)

    # 1. Retrieve specific compilation by ID
    retrieved = store.get_compilation("comp-uuid-2")
    assert retrieved is not None
    assert retrieved.status == "SUCCESS"
    assert retrieved.confidence == 0.95

    # Retrieve non-existent
    assert store.get_compilation("non-existent") is None

    # 2. List history by bundle ID
    history_abc = store.list_compilations("bundle-uuid-abc")
    assert len(history_abc) == 2
    assert history_abc[0].compilation_id == "comp-uuid-2"  # ordered by timestamp DESC
    assert history_abc[1].compilation_id == "comp-uuid-1"

    history_other = store.list_compilations("bundle-uuid-other")
    assert len(history_other) == 1
    assert history_other[0].compilation_id == "comp-uuid-3"

    history_empty = store.list_compilations("bundle-uuid-non-existent")
    assert len(history_empty) == 0
