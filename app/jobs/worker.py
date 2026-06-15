import json
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.observability.metrics import metrics_collector
from app.observability.logging import logger
from app.runtime.engine import ExecutionEngine
from app.runtime.models import ExecutionStatus, ApprovalStatus
from app.jobs.queue import JobQueue
from app.jobs.models import JobRecord, JobStatus
from app.jobs.retry import ExponentialBackoffPolicy


class BackgroundWorker:
    """Daemon worker thread polling and processing background workflow execution jobs."""

    def __init__(self, queue: JobQueue, engine: ExecutionEngine, poll_interval: float = 1.0):
        self.queue = queue
        self.engine = engine
        self.poll_interval = poll_interval
        self.hostname = socket.gethostname()
        self.worker_id = f"worker-{self.hostname}-{uuid.uuid4().hex[:8]}"
        self.stopped = False
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Starts the background polling worker thread and runs orphaned job recovery."""
        with self._lock:
            if self.thread is not None:
                return
            self.stopped = False
            
            # Recover orphaned jobs on startup before worker loop starts
            self.recover_orphaned_jobs()

            self.thread = threading.Thread(
                target=self._loop, name=f"WorkerThread-{self.worker_id}", daemon=True
            )
            self.thread.start()
            logger.info(f"Worker {self.worker_id} started background loop")

    def stop(self) -> None:
        """Stops the background polling worker loop gracefully."""
        with self._lock:
            self.stopped = True
            if self.thread:
                self.thread.join(timeout=5.0)
                self.thread = None
            logger.info(f"Worker {self.worker_id} stopped background loop")

    def recover_orphaned_jobs(self) -> None:
        """Finds running jobs from previous crashed/stopped sessions and resets them to QUEUED or marks them FAILED."""
        with self.queue.lock:
            conn = sqlite3.connect(self.queue.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT job_id, tenant_id, attempt_count, max_attempts, created_by
                    FROM jobs
                    WHERE status = 'RUNNING' AND (worker_id IS NULL OR worker_id != 'HIL_SUSPENDED')
                    """
                )
                rows = cursor.fetchall()
                for row in rows:
                    job_id, tenant_id, attempt_count, max_attempts, created_by = row
                    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    
                    if attempt_count < max_attempts:
                        cursor.execute(
                            "UPDATE jobs SET status = 'QUEUED', worker_id = NULL, run_at = NULL WHERE job_id = ?",
                            (job_id,),
                        )
                        self._log_audit(
                            tenant_id=tenant_id,
                            action="JOB_RETRIED",
                            resource_id=job_id,
                            status="SUCCESS",
                            user_id="system",
                            details={"info": "Orphaned job reset to QUEUED on system restart"},
                        )
                    else:
                        cursor.execute(
                            "UPDATE jobs SET status = 'FAILED', completed_at = ?, last_error = ? WHERE job_id = ?",
                            (now_str, "Job execution orphaned and exceeded maximum attempts", job_id),
                        )
                        self._log_audit(
                            tenant_id=tenant_id,
                            action="JOB_FAILED",
                            resource_id=job_id,
                            status="ERROR",
                            user_id="system",
                            details={"error": "Job execution orphaned and exceeded maximum attempts"},
                        )
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"Error during orphaned job recovery: {str(e)}")
            finally:
                conn.close()

    def _loop(self) -> None:
        """Background poll loop."""
        metrics_collector.record_active_workers(1)
        try:
            while not self.stopped:
                try:
                    job = self.queue.dequeue(self.worker_id)
                    if not job:
                        time.sleep(self.poll_interval)
                        continue

                    self._process_job(job)
                except Exception as e:
                    logger.error(f"BackgroundWorker loop exception: {str(e)}")
                    time.sleep(self.poll_interval)
        finally:
            metrics_collector.record_active_workers(-1)

    def _process_job(self, job: JobRecord) -> None:
        tenant_id = job.tenant_id
        action = job.payload.get("action", "execute")

        self._log_audit(
            tenant_id=tenant_id,
            action="JOB_STARTED",
            resource_id=job.job_id,
            status="SUCCESS",
            user_id=job.created_by,
            details={"execution_id": job.execution_id, "attempt": job.attempt_count},
        )

        start_time = time.perf_counter()
        try:
            if action == "resume":
                resume_data = job.payload.get("resume_data", {})
                decider_id = resume_data.get("decider_id", "system")
                decision = resume_data.get("decision", "APPROVED")
                decision_enum = ApprovalStatus.APPROVED if decision == "APPROVED" else ApprovalStatus.REJECTED

                exec_record = self.engine.resume(
                    execution_id=job.execution_id,
                    decider_id=decider_id,
                    decision=decision_enum,
                    tenant_id=tenant_id,
                )
            else:
                # Clean prior run history for retried execution
                if job.attempt_count > 1:
                    with self.queue.lock:
                        conn = sqlite3.connect(self.queue.db_path)
                        try:
                            cursor = conn.cursor()
                            cursor.execute("DELETE FROM node_executions WHERE execution_id = ?", (job.execution_id,))
                            cursor.execute("DELETE FROM approvals WHERE execution_id = ?", (job.execution_id,))
                            conn.commit()
                        except Exception as ex:
                            conn.rollback()
                            logger.error(f"Failed to clear node executions on retry: {str(ex)}")
                        finally:
                            conn.close()

                exec_record = self.engine.execute(
                    tenant_id=tenant_id,
                    bundle_id=job.bundle_id,
                    version=job.bundle_version,
                    triggered_by=job.created_by,
                    initial_input=job.payload.get("initial_input", {}),
                    execution_id=job.execution_id,
                )

            duration_ms = (time.perf_counter() - start_time) * 1000.0

            if exec_record.status == ExecutionStatus.COMPLETED:
                job.status = JobStatus.COMPLETED
                job.completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                self.queue.update_job(tenant_id, job)

                metrics_collector.record_job_success(tenant_id, duration_ms)
                self._log_audit(tenant_id, "JOB_COMPLETED", job.job_id, "SUCCESS", job.created_by)

            elif exec_record.status == ExecutionStatus.FAILED:
                self._handle_failure(job, "Workflow execution status failed", duration_ms)

            elif exec_record.status == ExecutionStatus.RUNNING:
                # Workflow paused on human approval gate
                job.status = JobStatus.RUNNING
                job.worker_id = "HIL_SUSPENDED"
                self.queue.update_job(tenant_id, job)

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            self._handle_failure(job, str(e), duration_ms)

    def _handle_failure(self, job: JobRecord, error_msg: str, duration_ms: float) -> None:
        tenant_id = job.tenant_id
        job.last_error = error_msg

        if job.attempt_count < job.max_attempts:
            # Exponential backoff calculation
            policy = ExponentialBackoffPolicy(max_attempts=job.max_attempts)
            next_run = policy.get_next_run_time(job.attempt_count)

            job.status = JobStatus.RETRYING
            job.run_at = next_run.isoformat().replace("+00:00", "Z")
            self.queue.update_job(tenant_id, job)

            metrics_collector.record_job_retry(tenant_id)
            self._log_audit(
                tenant_id=tenant_id,
                action="JOB_RETRIED",
                resource_id=job.job_id,
                status="SUCCESS",
                user_id=job.created_by,
                details={"error": error_msg, "next_run_at": job.run_at},
            )
        else:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self.queue.update_job(tenant_id, job)

            metrics_collector.record_job_failure(tenant_id, duration_ms)
            self._log_audit(
                tenant_id=tenant_id,
                action="JOB_FAILED",
                resource_id=job.job_id,
                status="ERROR",
                user_id=job.created_by,
                details={"error": error_msg},
            )

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
            resource_type="job",
            resource_id=resource_id,
            status=status,
            details=details or {},
            tenant_id=tenant_id,
        )
        audit_repo.save_audit_event(tenant_id, record)
