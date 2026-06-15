import os
import tempfile
import pytest
from datetime import datetime, timezone

from app.storage.sqlite import SQLiteRepository
from app.jobs.models import ScheduleRecord
from app.jobs.queue import JobQueue
from app.jobs.scheduler import CronScheduler


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_schedule_tenant_isolation(temp_db):
    repo = SQLiteRepository(temp_db)
    queue = JobQueue(repo)
    scheduler = CronScheduler(repo, queue)
    
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Create schedule for Tenant A
    sch_a = ScheduleRecord(
        schedule_id="sch-a",
        tenant_id="tenant-a",
        bundle_id="bundle-a",
        bundle_version=1,
        cron_expression="*/5 * * * *",
        enabled=True,
        next_run_at=now_str,
        created_by="key-a",
        created_at=now_str,
    )
    
    # Create schedule for Tenant B
    sch_b = ScheduleRecord(
        schedule_id="sch-b",
        tenant_id="tenant-b",
        bundle_id="bundle-b",
        bundle_version=1,
        cron_expression="*/5 * * * *",
        enabled=True,
        next_run_at=now_str,
        created_by="key-b",
        created_at=now_str,
    )
    
    scheduler.save_schedule(sch_a)
    scheduler.save_schedule(sch_b)
    
    # 1. Tenant A cannot see Tenant B's schedules
    list_a = scheduler.list_schedules("tenant-a")
    assert len(list_a) == 1
    assert list_a[0].schedule_id == "sch-a"
    
    list_b = scheduler.list_schedules("tenant-b")
    assert len(list_b) == 1
    assert list_b[0].schedule_id == "sch-b"

    # 2. Get cross-tenant schedule returns None
    assert scheduler.get_schedule("tenant-a", "sch-b") is None
    assert scheduler.get_schedule("tenant-b", "sch-b") is not None

    # 3. Delete cross-tenant schedule fails
    assert scheduler.delete_schedule("tenant-a", "sch-b") is False
    assert scheduler.delete_schedule("tenant-b", "sch-b") is True
