import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from app.storage.sqlite import SQLiteRepository
from app.jobs.models import JobRecord, JobStatus


class JobQueue:
    """SQLite-backed, thread-safe, durable queue management service."""

    def __init__(self, repo: SQLiteRepository):
        self.repo = repo
        self.db_path = repo.db_path
        self.lock = repo.lock

    def enqueue(self, job: JobRecord) -> bool:
        """
        Durable enqueue of a job.
        Uses INSERT INTO ON CONFLICT(schedule_fire_id) DO NOTHING to prevent duplicate schedule executions.
        Returns True if the job was successfully enqueued, False if ignored due to duplication.
        """
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO jobs (
                        job_id, tenant_id, execution_id, bundle_id, bundle_version, status,
                        attempt_count, max_attempts, created_at, started_at, completed_at,
                        last_error, payload, run_at, created_by, worker_id, priority, schedule_fire_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(schedule_fire_id) DO NOTHING
                    """,
                    (
                        job.job_id,
                        job.tenant_id,
                        job.execution_id,
                        job.bundle_id,
                        job.bundle_version,
                        job.status.value,
                        job.attempt_count,
                        job.max_attempts,
                        job.created_at,
                        job.started_at,
                        job.completed_at,
                        job.last_error,
                        json.dumps(job.payload),
                        job.run_at,
                        job.created_by,
                        job.worker_id,
                        job.priority,
                        job.schedule_fire_id,
                    ),
                )
                conn.commit()
                # If rowcount is 0, it means it conflicted and was ignored
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def dequeue(self, worker_id: str) -> Optional[JobRecord]:
        """
        Atomically fetches the next eligible job, locks it with worker_id, and sets status to RUNNING.
        Uses 'BEGIN IMMEDIATE' to serialize queue access across multiple worker threads/processes.
        """
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA busy_timeout = 30000;")
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE;")

                now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

                # Fetch next available job where run_at is in the past or NULL, ordered by priority
                cursor.execute(
                    """
                    SELECT job_id, tenant_id, execution_id, bundle_id, bundle_version, status,
                           attempt_count, max_attempts, created_at, started_at, completed_at,
                           last_error, payload, run_at, created_by, worker_id, priority, schedule_fire_id
                    FROM jobs
                    WHERE (status = 'QUEUED' OR status = 'RETRYING')
                      AND (run_at IS NULL OR run_at <= ?)
                    ORDER BY priority ASC, created_at ASC
                    LIMIT 1
                    """,
                    (now_str,),
                )
                row = cursor.fetchone()
                if not row:
                    cursor.execute("COMMIT;")
                    return None

                job_id = row[0]
                new_attempt = row[6] + 1

                # Lock the job
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = 'RUNNING', started_at = ?, worker_id = ?, attempt_count = ?
                    WHERE job_id = ?
                    """,
                    (now_str, worker_id, new_attempt, job_id),
                )
                cursor.execute("COMMIT;")

                return JobRecord(
                    job_id=row[0],
                    tenant_id=row[1],
                    execution_id=row[2],
                    bundle_id=row[3],
                    bundle_version=row[4],
                    status=JobStatus.RUNNING,
                    attempt_count=new_attempt,
                    max_attempts=row[7],
                    created_at=row[8],
                    started_at=now_str,
                    completed_at=row[10],
                    last_error=row[11],
                    payload=json.loads(row[12]) if row[12] else {},
                    run_at=row[13],
                    created_by=row[14],
                    worker_id=worker_id,
                    priority=row[16],
                    schedule_fire_id=row[17],
                )
            except Exception as e:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                raise e
            finally:
                conn.close()

    def get_job(self, tenant_id: str, job_id: str) -> Optional[JobRecord]:
        """Lookup a job record within a tenant scope."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT job_id, tenant_id, execution_id, bundle_id, bundle_version, status,
                           attempt_count, max_attempts, created_at, started_at, completed_at,
                           last_error, payload, run_at, created_by, worker_id, priority, schedule_fire_id
                    FROM jobs
                    WHERE tenant_id = ? AND job_id = ?
                    """,
                    (tenant_id, job_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return JobRecord(
                    job_id=row[0],
                    tenant_id=row[1],
                    execution_id=row[2],
                    bundle_id=row[3],
                    bundle_version=row[4],
                    status=JobStatus(row[5]),
                    attempt_count=row[6],
                    max_attempts=row[7],
                    created_at=row[8],
                    started_at=row[9],
                    completed_at=row[10],
                    last_error=row[11],
                    payload=json.loads(row[12]) if row[12] else {},
                    run_at=row[13],
                    created_by=row[14],
                    worker_id=row[15],
                    priority=row[16],
                    schedule_fire_id=row[17],
                )
            finally:
                conn.close()

    def list_jobs(self, tenant_id: str) -> List[JobRecord]:
        """List all jobs in a tenant organization."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT job_id, tenant_id, execution_id, bundle_id, bundle_version, status,
                           attempt_count, max_attempts, created_at, started_at, completed_at,
                           last_error, payload, run_at, created_by, worker_id, priority, schedule_fire_id
                    FROM jobs
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        JobRecord(
                            job_id=row[0],
                            tenant_id=row[1],
                            execution_id=row[2],
                            bundle_id=row[3],
                            bundle_version=row[4],
                            status=JobStatus(row[5]),
                            attempt_count=row[6],
                            max_attempts=row[7],
                            created_at=row[8],
                            started_at=row[9],
                            completed_at=row[10],
                            last_error=row[11],
                            payload=json.loads(row[12]) if row[12] else {},
                            run_at=row[13],
                            created_by=row[14],
                            worker_id=row[15],
                            priority=row[16],
                            schedule_fire_id=row[17],
                        )
                    )
                return results
            finally:
                conn.close()

    def cancel(self, tenant_id: str, job_id: str) -> bool:
        """Cancel a QUEUED or RETRYING job."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = 'CANCELLED', completed_at = ?
                    WHERE tenant_id = ? AND job_id = ? AND (status = 'QUEUED' OR status = 'RETRYING')
                    """,
                    (now_str, tenant_id, job_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def update_job(self, tenant_id: str, job: JobRecord) -> None:
        """Persist updates to a job record."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = ?, attempt_count = ?, started_at = ?, completed_at = ?, last_error = ?, run_at = ?, worker_id = ?, payload = ?
                    WHERE tenant_id = ? AND job_id = ?
                    """,
                    (
                        job.status.value,
                        job.attempt_count,
                        job.started_at,
                        job.completed_at,
                        job.last_error,
                        job.run_at,
                        job.worker_id,
                        json.dumps(job.payload),
                        tenant_id,
                        job.job_id,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_queue_depth(self, tenant_id: Optional[str] = None) -> int:
        """Get the count of active QUEUED/RETRYING jobs in the database."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                if tenant_id:
                    cursor.execute(
                        "SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND (status = 'QUEUED' OR status = 'RETRYING')",
                        (tenant_id,),
                    )
                else:
                    cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'QUEUED' OR status = 'RETRYING'")
                return cursor.fetchone()[0]
            finally:
                conn.close()

    def get_oldest_queued_job_age(self, tenant_id: Optional[str] = None) -> float:
        """Get the age in seconds of the oldest queued job that is due."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                if tenant_id:
                    cursor.execute(
                        """
                        SELECT created_at FROM jobs 
                        WHERE tenant_id = ? AND (status = 'QUEUED' OR status = 'RETRYING') 
                          AND (run_at IS NULL OR run_at <= ?)
                        ORDER BY created_at ASC LIMIT 1
                        """,
                        (tenant_id, now_str),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT created_at FROM jobs 
                        WHERE (status = 'QUEUED' OR status = 'RETRYING') 
                          AND (run_at IS NULL OR run_at <= ?)
                        ORDER BY created_at ASC LIMIT 1
                        """,
                        (now_str,),
                    )
                row = cursor.fetchone()
                if not row:
                    return 0.0
                created_dt = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - created_dt).total_seconds()
            finally:
                conn.close()
