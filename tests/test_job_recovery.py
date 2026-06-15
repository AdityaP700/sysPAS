import os
import tempfile
from datetime import datetime, timezone
import pytest

from app.storage.sqlite import SQLiteRepository
from app.runtime.runner import MockQueryRunner
from app.runtime.engine import ExecutionEngine
from app.storage.bundle_store import BundleStore
from app.jobs.models import JobRecord, JobStatus
from app.jobs.queue import JobQueue
from app.jobs.worker import BackgroundWorker


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_orphaned_job_recovery(temp_db):
    repo = SQLiteRepository(temp_db)
    bundle_store = BundleStore(repo)
    engine = ExecutionEngine(repo, bundle_store, None, MockQueryRunner())
    queue = JobQueue(repo)
    
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # 1. Orphaned job 1: RUNNING, attempt < max_attempts -> Should reset to QUEUED
    j1 = JobRecord(
        job_id="job-reset",
        tenant_id="tenant-1",
        execution_id="exec-1",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        created_at=now_str,
        created_by="key-1",
        worker_id="worker-old",
    )
    
    # 2. Orphaned job 2: RUNNING, attempt == max_attempts -> Should transition to FAILED
    j2 = JobRecord(
        job_id="job-fail",
        tenant_id="tenant-1",
        execution_id="exec-2",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.RUNNING,
        attempt_count=3,
        max_attempts=3,
        created_at=now_str,
        created_by="key-1",
        worker_id="worker-old",
    )
    
    # 3. Paused job 3: RUNNING, worker_id = HIL_SUSPENDED -> Should remain unchanged
    j3 = JobRecord(
        job_id="job-hil",
        tenant_id="tenant-1",
        execution_id="exec-3",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        created_at=now_str,
        created_by="key-1",
        worker_id="HIL_SUSPENDED",
    )
    
    queue.enqueue(j1)
    queue.enqueue(j2)
    queue.enqueue(j3)
    
    # Force set status = RUNNING and worker_id because enqueue might insert them as QUEUED
    # Let's update them in DB directly
    with queue.lock:
        import sqlite3
        conn = sqlite3.connect(queue.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE jobs SET status = 'RUNNING', worker_id = 'worker-old' WHERE job_id IN ('job-reset', 'job-fail')")
            cursor.execute("UPDATE jobs SET status = 'RUNNING', worker_id = 'HIL_SUSPENDED' WHERE job_id = 'job-hil'")
            conn.commit()
        finally:
            conn.close()

    worker = BackgroundWorker(queue, engine)
    worker.recover_orphaned_jobs()
    
    # Assertions
    r1 = queue.get_job("tenant-1", "job-reset")
    assert r1.status == JobStatus.QUEUED
    assert r1.worker_id is None
    
    r2 = queue.get_job("tenant-1", "job-fail")
    assert r2.status == JobStatus.FAILED
    
    r3 = queue.get_job("tenant-1", "job-hil")
    assert r3.status == JobStatus.RUNNING
    assert r3.worker_id == "HIL_SUSPENDED"
