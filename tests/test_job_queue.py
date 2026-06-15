import os
import tempfile
from datetime import datetime, timezone
import pytest

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


def test_enqueue_and_dequeue_priority(temp_db):
    repo = SQLiteRepository(temp_db)
    queue = JobQueue(repo)
    
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Enqueue low priority job
    job_low = JobRecord(
        job_id="job-low",
        tenant_id="tenant-1",
        execution_id="exec-low",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.QUEUED,
        created_at=now,
        created_by="key-1",
        priority=200,
    )
    
    # Enqueue high priority job
    job_high = JobRecord(
        job_id="job-high",
        tenant_id="tenant-1",
        execution_id="exec-high",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.QUEUED,
        created_at=now,
        created_by="key-1",
        priority=50,
    )
    
    queue.enqueue(job_low)
    queue.enqueue(job_high)
    
    assert queue.get_queue_depth() == 2

    # Dequeue should select the high priority job first
    dq1 = queue.dequeue("worker-1")
    assert dq1 is not None
    assert dq1.job_id == "job-high"
    assert dq1.status == JobStatus.RUNNING
    assert dq1.worker_id == "worker-1"
    
    dq2 = queue.dequeue("worker-1")
    assert dq2 is not None
    assert dq2.job_id == "job-low"
    
    assert queue.dequeue("worker-1") is None
    assert queue.get_queue_depth() == 0


def test_cancel_job(temp_db):
    repo = SQLiteRepository(temp_db)
    queue = JobQueue(repo)
    
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    job = JobRecord(
        job_id="job-cancel",
        tenant_id="tenant-1",
        execution_id="exec-cancel",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.QUEUED,
        created_at=now,
        created_by="key-1",
        priority=100,
    )
    
    queue.enqueue(job)
    assert queue.cancel("tenant-1", "job-cancel") is True
    
    # Job should now be CANCELLED and not eligible for dequeue
    fetched = queue.get_job("tenant-1", "job-cancel")
    assert fetched.status == JobStatus.CANCELLED
    assert queue.dequeue("worker-1") is None
