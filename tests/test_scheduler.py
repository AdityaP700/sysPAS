import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
import pytest

from app.storage.sqlite import SQLiteRepository
from app.jobs.models import ScheduleRecord, JobRecord, JobStatus
from app.jobs.queue import JobQueue
from app.jobs.scheduler import get_next_run, CronScheduler


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_cron_parsing_next_run():
    # Base datetime: 2026-06-13T17:33:00Z
    base = datetime(2026, 6, 13, 17, 33, tzinfo=timezone.utc)
    
    # 1. Test every 5 minutes cron pattern: */5 * * * *
    # Expect 17:35
    n1 = get_next_run("*/5 * * * *", base)
    assert n1.minute == 35
    assert n1.hour == 17
    
    # 2. Test hourly cron pattern: 0 * * * *
    # Expect 18:00
    n2 = get_next_run("0 * * * *", base)
    assert n2.minute == 0
    assert n2.hour == 18

    # 3. Test daily cron pattern: 0 0 * * *
    # Expect 2026-06-14T00:00
    n3 = get_next_run("0 0 * * *", base)
    assert n3.minute == 0
    assert n3.hour == 0
    assert n3.day == 14


def test_scheduler_misfire_lag(temp_db):
    repo = SQLiteRepository(temp_db)
    queue = JobQueue(repo)
    scheduler = CronScheduler(repo, queue, max_schedule_lag_minutes=60)
    
    now = datetime.now(timezone.utc)
    # Set next_run_at to 90 minutes in the past (violates 60 minutes limit)
    past_run = now - timedelta(minutes=90)
    past_run_str = past_run.isoformat().replace("+00:00", "Z")
    
    schedule = ScheduleRecord(
        schedule_id="sch-misfire",
        tenant_id="tenant-1",
        bundle_id="bundle-1",
        bundle_version=1,
        cron_expression="*/5 * * * *",
        enabled=True,
        next_run_at=past_run_str,
        created_by="key-admin",
        created_at=past_run_str,
    )
    
    scheduler.save_schedule(schedule)
    
    # Evaluate schedules -> should trigger misfire skip, and NOT create a job
    scheduler._evaluate_schedules()
    
    # Assert queue remains empty (run was skipped)
    assert queue.get_queue_depth() == 0
    
    # Check that schedule's next_run_at was advanced to future
    updated = scheduler.get_schedule("tenant-1", "sch-misfire")
    updated_dt = datetime.fromisoformat(updated.next_run_at.replace("Z", "+00:00"))
    assert updated_dt > now


def test_execution_deduplication(temp_db):
    repo = SQLiteRepository(temp_db)
    queue = JobQueue(repo)
    
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Create two jobs with identical schedule_fire_id
    j1 = JobRecord(
        job_id="job-1",
        tenant_id="tenant-1",
        execution_id="exec-1",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.QUEUED,
        created_at=now_str,
        created_by="key-1",
        schedule_fire_id="fire_sch-1_2026-06-13T18:00:00Z"
    )
    
    j2 = JobRecord(
        job_id="job-2",
        tenant_id="tenant-1",
        execution_id="exec-2",
        bundle_id="bundle-1",
        bundle_version=1,
        status=JobStatus.QUEUED,
        created_at=now_str,
        created_by="key-1",
        schedule_fire_id="fire_sch-1_2026-06-13T18:00:00Z" # same fire ID
    )
    
    # Enqueue first -> Success
    res1 = queue.enqueue(j1)
    assert res1 is True
    
    # Enqueue second -> Ignored by DB constraint
    res2 = queue.enqueue(j2)
    assert res2 is False
    
    # Queue depth should only be 1
    assert queue.get_queue_depth() == 1
