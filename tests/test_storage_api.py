import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.web.main import app
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.storage.compilation_store import CompilationStore
from app.storage.trace_store import TraceStore
from app.service.runbook_service import RunbookService
from app.web.dependencies import (
    get_runbook_service,
    get_bundle_store,
    get_compilation_store,
    get_trace_store,
)


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


@pytest.fixture(autouse=True)
def setup_test_storage(temp_db_file):
    """Override FastAPI dependencies with isolated test storage database."""
    repo = SQLiteRepository(temp_db_file)
    bundle_store = BundleStore(repo)
    compilation_store = CompilationStore(repo)
    trace_store = TraceStore(repo)
    service = RunbookService(
        repo=repo,
        bundle_store=bundle_store,
        compilation_store=compilation_store,
        trace_store=trace_store,
    )

    app.dependency_overrides[get_runbook_service] = lambda: service
    app.dependency_overrides[get_bundle_store] = lambda: bundle_store
    app.dependency_overrides[get_compilation_store] = lambda: compilation_store
    app.dependency_overrides[get_trace_store] = lambda: trace_store

    yield repo, bundle_store, compilation_store, trace_store, service

    app.dependency_overrides.clear()


def test_persistence_api_flow():
    """Test full API compile, retrieve, history listing, trace retrieval, and deletion flow."""
    client = TestClient(app)

    # 1. Compile runbook
    runbook_md = (
        "# API Persistence Runbook\n"
        "## Steps\n"
        "1. Check spikes in authentication failures [DETECTION] {data_source=auth_logs}\n"
    )
    payload = {
        "content": runbook_md,
        "filename": "api_persistence.md",
    }
    compile_resp = client.post("/compile", json=payload)
    assert compile_resp.status_code == 200
    compile_data = compile_resp.json()
    assert compile_data["status"] == "SUCCESS"
    assert compile_data["runbook_name"] == "API Persistence Runbook"

    # 2. Get list of bundles
    bundles_resp = client.get("/bundles")
    assert bundles_resp.status_code == 200
    bundles_list = bundles_resp.json()
    assert len(bundles_list) == 1
    bundle_rec = bundles_list[0]
    bundle_id = bundle_rec["bundle_id"]
    assert bundle_rec["bundle_name"] == "API Persistence Runbook"
    assert bundle_rec["version"] == 1

    # 3. Compile second version
    compile_resp_v2 = client.post("/compile", json=payload)
    assert compile_resp_v2.status_code == 200

    # 4. Get bundle version history
    versions_resp = client.get(f"/bundles/{bundle_id}/versions")
    assert versions_resp.status_code == 200
    versions_list = versions_resp.json()
    assert len(versions_list) == 2
    assert versions_list[0]["version"] == 1
    assert versions_list[1]["version"] == 2

    # 5. Get latest version of bundle
    bundle_details_resp = client.get(f"/bundles/{bundle_id}")
    assert bundle_details_resp.status_code == 200
    details_data = bundle_details_resp.json()
    assert details_data["bundle_id"] == bundle_id
    assert details_data["bundle"]["manifest"]["version"] == "2"

    # 6. Retrieve compilations
    # Let's inspect the compilations database manually via dependency injection to get compilation_id
    compilation_store = setup_test_storage
    # Wait, we can list compilations for this bundle_id by querying the DB
    # Let's check how we query from API. Since we don't have a list-compilations endpoint, 
    # we can fetch the compilation list directly from the database or query the specific compilation_id.
    # Let's list compilations for this bundle via the repo to get their IDs.
    import sqlite3
    db_path = app.dependency_overrides[get_bundle_store]().repo.db_path
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT compilation_id FROM compilations WHERE bundle_id = ?", (bundle_id,))
    compilation_ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    assert len(compilation_ids) == 2
    comp_id = compilation_ids[0]

    # Get compilation record via API
    comp_resp = client.get(f"/compilations/{comp_id}")
    assert comp_resp.status_code == 200
    comp_data = comp_resp.json()
    assert comp_data["compilation_id"] == comp_id
    assert comp_data["bundle_id"] == bundle_id
    assert comp_data["status"] == "SUCCESS"

    # Get compilation traces via API
    traces_resp = client.get(f"/compilations/{comp_id}/traces")
    assert traces_resp.status_code == 200
    traces_list = traces_resp.json()
    assert len(traces_list) == 1
    assert traces_list[0]["step_id"] == "1"
    assert traces_list[0]["optimized_spl"] is not None

    # Get non-existent compilation
    non_existent_comp_resp = client.get("/compilations/non-existent-id")
    assert non_existent_comp_resp.status_code == 404

    # 7. Delete bundle
    delete_resp = client.delete(f"/bundles/{bundle_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"deleted": True}

    # Delete again should raise 404
    delete_resp_2 = client.delete(f"/bundles/{bundle_id}")
    assert delete_resp_2.status_code == 404

    # Get bundle should now raise 404
    get_after_delete = client.get(f"/bundles/{bundle_id}")
    assert get_after_delete.status_code == 404
