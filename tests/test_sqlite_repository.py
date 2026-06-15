import os
import tempfile
import threading
import pytest
from app.storage.sqlite import SQLiteRepository
from app.storage.models import BundleRecord, CompilationRecord, TraceRecord


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


def test_sqlite_schema_creation(temp_db_file):
    """Verify that tables are created automatically on initialization."""
    repo = SQLiteRepository(temp_db_file)
    # Check that database contains the tables
    import sqlite3
    conn = sqlite3.connect(temp_db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()

    assert "bundles" in tables
    assert "compilations" in tables
    assert "traces" in tables


def test_sqlite_bundle_crud(temp_db_file):
    """Test saving, retrieving, listing, and deleting bundle records."""
    repo = SQLiteRepository(temp_db_file)
    
    # Save a bundle
    record = BundleRecord(
        bundle_id="uuid-1",
        bundle_name="Test SOP",
        version=1,
        created_at="2026-06-12T00:00:00Z",
        status="SUCCESS",
        payload={"data": "v1"},
    )
    repo.save_bundle(record)
    
    # Save a second version
    record_v2 = BundleRecord(
        bundle_id="uuid-1",
        bundle_name="Test SOP",
        version=2,
        created_at="2026-06-12T00:05:00Z",
        status="SUCCESS",
        payload={"data": "v2"},
    )
    repo.save_bundle(record_v2)

    # Get latest
    latest = repo.get_bundle("uuid-1")
    assert latest is not None
    assert latest.version == 2
    assert latest.payload == {"data": "v2"}

    # Get specific version
    v1_rec = repo.get_bundle("uuid-1", version=1)
    assert v1_rec is not None
    assert v1_rec.version == 1
    assert v1_rec.payload == {"data": "v1"}

    # Get versions list
    versions = repo.get_versions("uuid-1")
    assert len(versions) == 2
    assert [v.version for v in versions] == [1, 2]

    # List bundles (returns latest version of unique bundles)
    bundles = repo.list_bundles()
    assert len(bundles) == 1
    assert bundles[0].version == 2

    # Delete bundle
    deleted = repo.delete_bundle("uuid-1")
    assert deleted is True
    assert repo.get_bundle("uuid-1") is None
    assert len(repo.list_bundles()) == 0

    # Delete non-existent
    deleted_nonexistent = repo.delete_bundle("uuid-1")
    assert deleted_nonexistent is False


def test_sqlite_compilation_and_traces(temp_db_file):
    """Test save and get for compilations and step traces."""
    repo = SQLiteRepository(temp_db_file)

    comp = CompilationRecord(
        compilation_id="comp-1",
        bundle_id="uuid-1",
        timestamp="2026-06-12T00:00:00Z",
        duration_ms=150.0,
        confidence=0.85,
        status="PARTIAL",
    )
    repo.save_compilation(comp)

    # Retrieve compilation
    retrieved_comp = repo.get_compilation("comp-1")
    assert retrieved_comp is not None
    assert retrieved_comp.status == "PARTIAL"
    assert retrieved_comp.duration_ms == 150.0

    # Retrieve non-existent
    assert repo.get_compilation("non-existent") is None

    # List compilations
    repo.save_compilation(
        CompilationRecord(
            compilation_id="comp-2",
            bundle_id="uuid-1",
            timestamp="2026-06-12T00:10:00Z",
            duration_ms=200.0,
            confidence=0.9,
            status="SUCCESS",
        )
    )
    comps = repo.list_compilations("uuid-1")
    assert len(comps) == 2
    assert comps[0].compilation_id == "comp-2"  # ordered by timestamp DESC

    # Save and retrieve traces
    t1 = TraceRecord(
        trace_id="trace-1",
        compilation_id="comp-1",
        step_id="step_1",
        request_id="req-1",
        correlation_id="corr-1",
        payload={"step": 1},
    )
    t2 = TraceRecord(
        trace_id="trace-2",
        compilation_id="comp-1",
        step_id="step_2",
        request_id="req-1",
        correlation_id="corr-1",
        payload={"step": 2},
    )
    repo.save_trace(t1)
    repo.save_trace(t2)

    traces = repo.get_traces_by_compilation("comp-1")
    assert len(traces) == 2
    assert traces[0].trace_id == "trace-1"
    assert traces[1].step_id == "step_2"


def test_sqlite_repository_thread_safety(temp_db_file):
    """Verify thread-safe concurrent writes using RLock protection."""
    repo = SQLiteRepository(temp_db_file)
    threads = []
    errors = []

    def writer_thread(thread_idx: int):
        try:
            for i in range(10):
                # Write unique bundle versions
                rec = BundleRecord(
                    bundle_id=f"bundle-t-{thread_idx}",
                    bundle_name=f"Thread Bundle {thread_idx}",
                    version=i + 1,
                    created_at=f"2026-06-12T00:00:{i:02d}Z",
                    status="SUCCESS",
                    payload={"idx": i},
                )
                repo.save_bundle(rec)
        except Exception as e:
            errors.append(e)

    for idx in range(5):
        t = threading.Thread(target=writer_thread, args=(idx,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0, f"Encountered thread-safety database exceptions: {errors}"
    
    # Assert all unique thread bundles were written and latest version is 10
    bundles = repo.list_bundles()
    assert len(bundles) == 5
    for b in bundles:
        assert b.version == 10
