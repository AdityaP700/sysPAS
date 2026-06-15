import os
import tempfile
import pytest
from datetime import datetime, timezone

from app.storage.sqlite import SQLiteRepository
from app.jobs.models import JobRecord, JobStatus
from app.jobs.queue import JobQueue


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_job_tenant_isolation(temp_db):
    repo = SQLiteRepository(temp_db)
    queue = JobQueue(repo)
    
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Create job for Tenant A
    job_a = JobRecord(
        job_id="job-a",
        tenant_id="tenant-a",
        execution_id="exec-a",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.QUEUED,
        created_at=now_str,
        created_by="key-a",
    )
    
    # Create job for Tenant B
    job_b = JobRecord(
        job_id="job-b",
        tenant_id="tenant-b",
        execution_id="exec-b",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.QUEUED,
        created_at=now_str,
        created_by="key-b",
    )
    
    queue.enqueue(job_a)
    queue.enqueue(job_b)
    
    # List jobs for Tenant A -> should only show Tenant A's jobs
    list_a = queue.list_jobs("tenant-a")
    assert len(list_a) == 1
    assert list_a[0].job_id == "job-a"
    
    # List jobs for Tenant B -> should only show Tenant B's jobs
    list_b = queue.list_jobs("tenant-b")
    assert len(list_b) == 1
    assert list_b[0].job_id == "job-b"

    # Get job_b using Tenant A -> returns None or should not be visible
    assert queue.get_job("tenant-a", "job-b") is None
    assert queue.get_job("tenant-b", "job-b") is not None

    # Cancel job_b using Tenant A -> should not work (fails to update because of tenant check)
    assert queue.cancel("tenant-a", "job-b") is False
    assert queue.cancel("tenant-b", "job-b") is True
