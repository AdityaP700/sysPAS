import hashlib
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.observability.logging import logger
from app.storage.sqlite import SQLiteRepository
from app.jobs.models import ScheduleRecord, JobRecord, JobStatus
from app.jobs.queue import JobQueue


def cron_matches(field_pattern: str, val: int) -> bool:
    """Helper to check if a datetime field value matches a cron field pattern."""
    if field_pattern == "*":
        return True
    if field_pattern.startswith("*/"):
        try:
            step = int(field_pattern[2:])
            return val % step == 0
        except ValueError:
            return False
    if "," in field_pattern:
        parts = field_pattern.split(",")
        for part in parts:
            if part.isdigit() and int(part) == val:
                return True
        return False
    if field_pattern.isdigit():
        return int(field_pattern) == val
    return False


def get_next_run(cron_expr: str, base_dt: datetime) -> datetime:
    """
    Calculates the next execution datetime based on a 5-field cron string.
    Checks up to 1 year (525600 minutes) in the future.
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError("Invalid cron expression: must have 5 fields")
    min_pat, hour_pat, dom_pat, month_pat, dow_pat = parts

    # Check from the next minute, resetting seconds/microseconds
    curr = base_dt.replace(second=0, microsecond=0)
    for _ in range(525600):
        curr += timedelta(minutes=1)
        if not cron_matches(min_pat, curr.minute):
            continue
        if not cron_matches(hour_pat, curr.hour):
            continue
        if not cron_matches(dom_pat, curr.day):
            continue
        if not cron_matches(month_pat, curr.month):
            continue
        # cron DOW: 0 is Sunday, 1 is Monday ... 6 is Saturday
        cron_dow = curr.isoweekday() % 7
        if not cron_matches(dow_pat, cron_dow):
            continue
        return curr
    raise ValueError("No matching datetime found within 1 year")


class CronScheduler:
    """Durable scheduler service running cron evaluations in a background loop."""

    def __init__(
        self,
        repo: SQLiteRepository,
        queue: JobQueue,
        poll_interval: float = 5.0,
        max_schedule_lag_minutes: int = 60,
    ):
        self.repo = repo
        self.queue = queue
        self.db_path = repo.db_path
        self.lock = repo.lock
        self.poll_interval = poll_interval
        self.max_schedule_lag_minutes = max_schedule_lag_minutes
        self.stopped = False
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Starts the background cron scheduler thread."""
        with self._lock:
            if self.thread is not None:
                return
            self.stopped = False
            self.thread = threading.Thread(
                target=self._loop, name="CronSchedulerThread", daemon=True
            )
            self.thread.start()
            logger.info("CronScheduler started background loop")

    def stop(self) -> None:
        """Stops the background cron scheduler loop gracefully."""
        with self._lock:
            self.stopped = True
            if self.thread:
                self.thread.join(timeout=5.0)
                self.thread = None
            logger.info("CronScheduler stopped background loop")

    # --- Schedule PERSISTENCE CRUD Operations ---

    def save_schedule(self, record: ScheduleRecord) -> None:
        """Persist or overwrite a cron schedule record."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO schedules (
                        schedule_id, tenant_id, bundle_id, bundle_version, cron_expression,
                        enabled, next_run_at, created_by, created_at, last_triggered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(schedule_id) DO UPDATE SET
                        enabled = excluded.enabled,
                        next_run_at = excluded.next_run_at,
                        last_triggered_at = excluded.last_triggered_at
                    """,
                    (
                        record.schedule_id,
                        record.tenant_id,
                        record.bundle_id,
                        record.bundle_version,
                        record.cron_expression,
                        1 if record.enabled else 0,
                        record.next_run_at,
                        record.created_by,
                        record.created_at,
                        record.last_triggered_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_schedule(self, tenant_id: str, schedule_id: str) -> Optional[ScheduleRecord]:
        """Retrieve a specific schedule under a tenant organization."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT schedule_id, tenant_id, bundle_id, bundle_version, cron_expression,
                           enabled, next_run_at, created_by, created_at, last_triggered_at
                    FROM schedules
                    WHERE tenant_id = ? AND schedule_id = ?
                    """,
                    (tenant_id, schedule_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return ScheduleRecord(
                    schedule_id=row[0],
                    tenant_id=row[1],
                    bundle_id=row[2],
                    bundle_version=row[3],
                    cron_expression=row[4],
                    enabled=bool(row[5]),
                    next_run_at=row[6],
                    created_by=row[7],
                    created_at=row[8],
                    last_triggered_at=row[9],
                )
            finally:
                conn.close()

    def list_schedules(self, tenant_id: str) -> List[ScheduleRecord]:
        """List all configured schedules in a tenant organization."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT schedule_id, tenant_id, bundle_id, bundle_version, cron_expression,
                           enabled, next_run_at, created_by, created_at, last_triggered_at
                    FROM schedules
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        ScheduleRecord(
                            schedule_id=row[0],
                            tenant_id=row[1],
                            bundle_id=row[2],
                            bundle_version=row[3],
                            cron_expression=row[4],
                            enabled=bool(row[5]),
                            next_run_at=row[6],
                            created_by=row[7],
                            created_at=row[8],
                            last_triggered_at=row[9],
                        )
                    )
                return results
            finally:
                conn.close()

    def delete_schedule(self, tenant_id: str, schedule_id: str) -> bool:
        """Delete a schedule from the database."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM schedules WHERE tenant_id = ? AND schedule_id = ?",
                    (tenant_id, schedule_id),
                )
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    # --- Background Loop & Misfire Execution Engine ---

    def _loop(self) -> None:
        """Periodic background evaluation loop."""
        while not self.stopped:
            try:
                self._evaluate_schedules()
            except Exception as e:
                logger.error(f"Scheduler loop encountered error: {str(e)}")
            time.sleep(self.poll_interval)

    def _evaluate_schedules(self) -> None:
        """Finds due enabled schedules, runs misfire and queue triggers, and calculates the next occurrences."""
        now = datetime.now(timezone.utc)
        now_str = now.isoformat().replace("+00:00", "Z")

        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT schedule_id, tenant_id, bundle_id, bundle_version, cron_expression, next_run_at, created_by, created_at
                    FROM schedules
                    WHERE enabled = 1 AND next_run_at <= ?
                    """,
                    (now_str,),
                )
                due_rows = cursor.fetchall()
                if not due_rows:
                    return

                for row in due_rows:
                    schedule_id, tenant_id, bundle_id, bundle_version, cron_expression, next_run_at_str, created_by, created_at = row
                    
                    next_run_dt = datetime.fromisoformat(next_run_at_str.replace("Z", "+00:00"))
                    lag_delta = now - next_run_dt

                    # Calculate the next run time
                    new_next_run_dt = get_next_run(cron_expression, now)
                    new_next_run_str = new_next_run_dt.isoformat().replace("+00:00", "Z")

                    if lag_delta.total_seconds() > (self.max_schedule_lag_minutes * 60):
                        # Misfire condition: lag exceeds maximum lag threshold
                        cursor.execute(
                            """
                            UPDATE schedules
                            SET next_run_at = ?
                            WHERE schedule_id = ?
                            """,
                            (new_next_run_str, schedule_id),
                        )
                        self._log_audit(
                            tenant_id=tenant_id,
                            action="SCHEDULE_MISSED",
                            resource_id=schedule_id,
                            status="WARNING",
                            user_id="system",
                            details={
                                "info": f"Schedule misfire skipped. Lag: {lag_delta.total_seconds() / 60.0:.2f} mins exceeds threshold.",
                                "missed_run": next_run_at_str,
                            },
                        )
                        from app.observability.metrics import metrics_collector
                        metrics_collector.record_schedule_failure(tenant_id)
                        continue

                    # Trigger a run
                    execution_id = f"exec_{uuid.uuid4().hex[:12]}"
                    job_id = f"job_{uuid.uuid4().hex[:12]}"
                    
                    # Generate deterministic schedule_fire_id to prevent duplicate runs
                    schedule_fire_id = f"fire_{schedule_id}_{next_run_at_str}"

                    # Enqueue Job
                    job = JobRecord(
                        job_id=job_id,
                        tenant_id=tenant_id,
                        execution_id=execution_id,
                        bundle_id=bundle_id,
                        bundle_version=bundle_version,
                        status=JobStatus.QUEUED,
                        attempt_count=0,
                        max_attempts=3,
                        created_at=now_str,
                        created_by=created_by,
                        priority=100,
                        schedule_fire_id=schedule_fire_id,
                        payload={"action": "execute", "initial_input": {}},
                    )

                    # Create a PENDING execution record in DB first
                    cursor.execute(
                        """
                        INSERT INTO executions (execution_id, tenant_id, bundle_id, bundle_version, status, current_node_id, started_at, triggered_by, context_payload)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            execution_id,
                            tenant_id,
                            bundle_id,
                            bundle_version,
                            "PENDING",
                            None,
                            now_str,
                            created_by,
                            "{}",
                        ),
                    )

                    # Update schedule triggers
                    cursor.execute(
                        """
                        UPDATE schedules
                        SET last_triggered_at = ?, next_run_at = ?
                        WHERE schedule_id = ?
                        """,
                        (now_str, new_next_run_str, schedule_id),
                    )

                    # Atomic save in same lock context
                    try:
                        self.queue.enqueue(job)
                        
                        from app.observability.metrics import metrics_collector
                        metrics_collector.record_schedule_run(tenant_id)

                        self._log_audit(
                            tenant_id=tenant_id,
                            action="SCHEDULE_TRIGGERED",
                            resource_id=schedule_id,
                            status="SUCCESS",
                            user_id="system",
                            details={"job_id": job_id, "execution_id": execution_id, "fire_id": schedule_fire_id},
                        )
                    except Exception as eq:
                        logger.error(f"Failed to enqueue schedule job: {str(eq)}")
                        # Rollback is handled by the outer block

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def _log_audit(
        self,
        tenant_id: str,
        action: str,
        resource_id: str,
        status: str,
        user_id: str,
        details: Optional[dict] = None,
    ) -> None:
        from app.web.dependencies import get_audit_repository

        audit_repo = get_audit_repository()
        if not audit_repo:
            return
        from app.audit.models import AuditEventRecord

        record = AuditEventRecord(
            audit_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            request_id=str(uuid.uuid4()),
            correlation_id=str(uuid.uuid4()),
            user_id=user_id,
            role="SYSTEM",
            action=action,
            resource_type="schedule",
            resource_id=resource_id,
            status=status,
            details=details or {},
            tenant_id=tenant_id,
        )
        audit_repo.save_audit_event(tenant_id, record)
