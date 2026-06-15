import threading
from typing import Dict, List, Optional


class MetricsCollector:
    """Thread-safe collector for compiler, API, background jobs, and scheduler operational metrics."""

    def __init__(self):
        self._lock = threading.Lock()
        
        # API request counters (global)
        self.api_request_count = 0
        self.api_error_count = 0
        
        # Compiler counters (global)
        self.compilation_count = 0
        self.compilation_success_count = 0
        self.compilation_failure_count = 0
        self.total_duration_ms = 0.0

        # Tenant-specific counters
        self.tenant_api_requests: Dict[str, int] = {}
        self.tenant_api_errors: Dict[str, int] = {}
        self.tenant_compilations: Dict[str, int] = {}
        self.tenant_compilation_success: Dict[str, int] = {}
        self.tenant_compilation_failure: Dict[str, int] = {}
        self.tenant_total_duration_ms: Dict[str, float] = {}

        # Job and Scheduler metrics
        self.active_workers_count = 0
        self.tenant_jobs_created: Dict[str, int] = {}
        self.tenant_jobs_completed: Dict[str, int] = {}
        self.tenant_jobs_failed: Dict[str, int] = {}
        self.tenant_jobs_retried: Dict[str, int] = {}
        self.tenant_schedule_runs: Dict[str, int] = {}
        self.tenant_schedule_failures: Dict[str, int] = {}
        self.tenant_job_durations: Dict[str, List[float]] = {}

        # Query and Action Metrics
        self.queries_executed = 0
        self.queries_failed = 0
        self.actions_executed = 0
        self.actions_failed = 0
        self.tenant_queries_executed: Dict[str, int] = {}
        self.tenant_queries_failed: Dict[str, int] = {}
        self.tenant_actions_executed: Dict[str, int] = {}
        self.tenant_actions_failed: Dict[str, int] = {}
        self.tenant_query_durations: Dict[str, List[float]] = {}
        self.tenant_action_durations: Dict[str, List[float]] = {}

        self._queue_depth_callback = None
        self._oldest_job_age_callback = None

    @property
    def average_duration_ms(self) -> float:
        """Dynamically calculate the average compilation duration in milliseconds."""
        with self._lock:
            if self.compilation_count == 0:
                return 0.0
            return round(self.total_duration_ms / self.compilation_count, 2)

    def average_duration_ms_for_tenant(self, tenant_id: str) -> float:
        """Calculate average duration for a specific tenant workspace."""
        with self._lock:
            count = self.tenant_compilations.get(tenant_id, 0)
            if count == 0:
                return 0.0
            total_time = self.tenant_total_duration_ms.get(tenant_id, 0.0)
            return round(total_time / count, 2)

    def record_api_request(self, tenant_id: str = "system"):
        """Increment the total and tenant-specific API request counts."""
        with self._lock:
            self.api_request_count += 1
            self.tenant_api_requests[tenant_id] = self.tenant_api_requests.get(tenant_id, 0) + 1

    def record_api_error(self, tenant_id: str = "system"):
        """Increment the global and tenant-specific API error counts."""
        with self._lock:
            self.api_error_count += 1
            self.tenant_api_errors[tenant_id] = self.tenant_api_errors.get(tenant_id, 0) + 1

    def record_compilation(self, success: bool, duration_ms: float, tenant_id: str = "system"):
        """Record the outcome and performance details of a compilation run."""
        with self._lock:
            self.compilation_count += 1
            self.total_duration_ms += duration_ms
            
            self.tenant_compilations[tenant_id] = self.tenant_compilations.get(tenant_id, 0) + 1
            self.tenant_total_duration_ms[tenant_id] = self.tenant_total_duration_ms.get(tenant_id, 0.0) + duration_ms

            if success:
                self.compilation_success_count += 1
                self.tenant_compilation_success[tenant_id] = self.tenant_compilation_success.get(tenant_id, 0) + 1
            else:
                self.compilation_failure_count += 1
                self.tenant_compilation_failure[tenant_id] = self.tenant_compilation_failure.get(tenant_id, 0) + 1

    # --- Job and Scheduler Metric Recording Methods ---

    def register_queue_callbacks(self, queue_depth_cb, oldest_job_age_cb):
        """Register callbacks to dynamically query database queue status without circular dependencies."""
        with self._lock:
            self._queue_depth_callback = queue_depth_cb
            self._oldest_job_age_callback = oldest_job_age_cb

    def get_queue_depth(self, tenant_id: Optional[str] = None) -> int:
        """Query the current queue depth."""
        with self._lock:
            if self._queue_depth_callback:
                return self._queue_depth_callback(tenant_id)
            return 0

    def get_oldest_queued_job_age_seconds(self, tenant_id: Optional[str] = None) -> float:
        """Query the age of the oldest queued job in the queue."""
        with self._lock:
            if self._oldest_job_age_callback:
                return self._oldest_job_age_callback(tenant_id)
            return 0.0

    def record_active_workers(self, delta: int):
        """Record a change in the count of active workers."""
        with self._lock:
            self.active_workers_count += delta

    def record_job_created(self, tenant_id: str):
        """Record the creation of a new background job."""
        with self._lock:
            self.tenant_jobs_created[tenant_id] = self.tenant_jobs_created.get(tenant_id, 0) + 1

    def record_job_success(self, tenant_id: str, duration_ms: float):
        """Record a successful background job run."""
        with self._lock:
            self.tenant_jobs_completed[tenant_id] = self.tenant_jobs_completed.get(tenant_id, 0) + 1
            if tenant_id not in self.tenant_job_durations:
                self.tenant_job_durations[tenant_id] = []
            self.tenant_job_durations[tenant_id].append(duration_ms)

    def record_job_failure(self, tenant_id: str, duration_ms: float):
        """Record a failed background job run."""
        with self._lock:
            self.tenant_jobs_failed[tenant_id] = self.tenant_jobs_failed.get(tenant_id, 0) + 1
            if tenant_id not in self.tenant_job_durations:
                self.tenant_job_durations[tenant_id] = []
            self.tenant_job_durations[tenant_id].append(duration_ms)

    def record_job_retry(self, tenant_id: str):
        """Record a job retry scheduling event."""
        with self._lock:
            self.tenant_jobs_retried[tenant_id] = self.tenant_jobs_retried.get(tenant_id, 0) + 1

    def record_schedule_run(self, tenant_id: str):
        """Record a successfully triggered cron schedule execution."""
        with self._lock:
            self.tenant_schedule_runs[tenant_id] = self.tenant_schedule_runs.get(tenant_id, 0) + 1

    def record_schedule_failure(self, tenant_id: str):
        """Record a cron schedule misfire or failure."""
        with self._lock:
            self.tenant_schedule_failures[tenant_id] = self.tenant_schedule_failures.get(tenant_id, 0) + 1

    def get_job_success_rate_for_tenant(self, tenant_id: str) -> float:
        """Calculate the job success rate percentage for a tenant workspace."""
        with self._lock:
            completed = self.tenant_jobs_completed.get(tenant_id, 0)
            failed = self.tenant_jobs_failed.get(tenant_id, 0)
            total = completed + failed
            if total == 0:
                return 100.0
            return round((completed / total) * 100.0, 2)

    def get_average_job_duration_for_tenant(self, tenant_id: str) -> float:
        """Calculate the average background job run time in milliseconds for a tenant workspace."""
        with self._lock:
            durations = self.tenant_job_durations.get(tenant_id, [])
            if not durations:
                return 0.0
            return round(sum(durations) / len(durations), 2)

    def record_query_execution(self, tenant_id: str, success: bool, duration_ms: float):
        """Record a query execution outcome and duration."""
        with self._lock:
            if success:
                self.queries_executed += 1
                self.tenant_queries_executed[tenant_id] = self.tenant_queries_executed.get(tenant_id, 0) + 1
            else:
                self.queries_failed += 1
                self.tenant_queries_failed[tenant_id] = self.tenant_queries_failed.get(tenant_id, 0) + 1
            if tenant_id not in self.tenant_query_durations:
                self.tenant_query_durations[tenant_id] = []
            self.tenant_query_durations[tenant_id].append(duration_ms)

    def record_action_execution(self, tenant_id: str, action_type: str, success: bool, duration_ms: float):
        """Record an action execution outcome, type, and duration."""
        with self._lock:
            if success:
                self.actions_executed += 1
                self.tenant_actions_executed[tenant_id] = self.tenant_actions_executed.get(tenant_id, 0) + 1
            else:
                self.actions_failed += 1
                self.tenant_actions_failed[tenant_id] = self.tenant_actions_failed.get(tenant_id, 0) + 1
            if tenant_id not in self.tenant_action_durations:
                self.tenant_action_durations[tenant_id] = []
            self.tenant_action_durations[tenant_id].append(duration_ms)

    def get_average_query_duration_for_tenant(self, tenant_id: str) -> float:
        """Calculate average query duration for a specific tenant workspace."""
        with self._lock:
            durations = self.tenant_query_durations.get(tenant_id, [])
            if not durations:
                return 0.0
            return round(sum(durations) / len(durations), 2)

    def get_average_action_duration_for_tenant(self, tenant_id: str) -> float:
        """Calculate average action duration for a specific tenant workspace."""
        with self._lock:
            durations = self.tenant_action_durations.get(tenant_id, [])
            if not durations:
                return 0.0
            return round(sum(durations) / len(durations), 2)

    def reset(self):
        """Reset all metric counters back to default values."""
        with self._lock:
            self.api_request_count = 0
            self.api_error_count = 0
            self.compilation_count = 0
            self.compilation_success_count = 0
            self.compilation_failure_count = 0
            self.total_duration_ms = 0.0
            self.active_workers_count = 0

            self.tenant_api_requests.clear()
            self.tenant_api_errors.clear()
            self.tenant_compilations.clear()
            self.tenant_compilation_success.clear()
            self.tenant_compilation_failure.clear()
            self.tenant_total_duration_ms.clear()

            self.tenant_jobs_created.clear()
            self.tenant_jobs_completed.clear()
            self.tenant_jobs_failed.clear()
            self.tenant_jobs_retried.clear()
            self.tenant_schedule_runs.clear()
            self.tenant_schedule_failures.clear()
            self.tenant_job_durations.clear()

            self.queries_executed = 0
            self.queries_failed = 0
            self.actions_executed = 0
            self.actions_failed = 0
            self.tenant_queries_executed.clear()
            self.tenant_queries_failed.clear()
            self.tenant_actions_executed.clear()
            self.tenant_actions_failed.clear()
            self.tenant_query_durations.clear()
            self.tenant_action_durations.clear()


# Singleton instance of metrics collector
metrics_collector = MetricsCollector()
