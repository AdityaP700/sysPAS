import time
import threading
import logging
from datetime import datetime, timezone
from typing import Optional

from app.storage.sqlite import SQLiteRepository
from app.connectors.service import ConnectorService

logger = logging.getLogger(__name__)


class ConnectorHealthScheduler:
    """Background thread scheduler that periodically runs health checks on all registered connectors."""

    def __init__(self, repo: SQLiteRepository, interval_seconds: int = 300):
        self.repo = repo
        self.service = ConnectorService(repo)
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background health checker loop."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ConnectorHealthChecker")
        self._thread.start()
        logger.info("ConnectorHealthScheduler background thread started.")

    def stop(self) -> None:
        """Stop the background health checker loop."""
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=5)
        self._thread = None
        logger.info("ConnectorHealthScheduler background thread stopped.")

    def run_once(self) -> None:
        """Run health checks on all connectors across all tenants immediately."""
        try:
            tenants = self.repo.list_tenants()
        except Exception as e:
            logger.error(f"Health check scheduler failed to list tenants: {str(e)}")
            return

        for tenant in tenants:
            tenant_id = tenant.tenant_id
            try:
                connectors = self.repo.list_connectors(tenant_id)
            except Exception as e:
                logger.error(f"Health check scheduler failed to list connectors for tenant {tenant_id}: {str(e)}")
                continue

            for record in connectors:
                if not record.enabled:
                    continue
                
                now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                try:
                    # Instantiate connector
                    instance = self.service._get_connector_instance(tenant_id, record)
                    success = instance.check_health()
                    
                    record.last_health_check = now_str
                    if success:
                        record.health_status = "HEALTHY"
                        record.last_success_at = now_str
                        record.consecutive_failures = 0
                        # Clear validation error
                        record.validation_error = None
                    else:
                        record.health_status = "UNHEALTHY"
                        record.consecutive_failures += 1
                        record.validation_error = "Health check returned false"
                except Exception as e:
                    record.last_health_check = now_str
                    record.health_status = "UNHEALTHY"
                    record.consecutive_failures += 1
                    record.validation_error = f"Health check failed: {str(e)}"
                    logger.warning(f"Health check failed for connector '{record.connector_id}': {str(e)}")

                try:
                    self.repo.save_connector(tenant_id, record)
                except Exception as e:
                    logger.error(f"Failed to save health check status for connector '{record.connector_id}': {str(e)}")

    def _run_loop(self) -> None:
        """Internal loop executing health checks periodically."""
        # Initial wait or run immediately? Let's run immediately first
        self.run_once()
        
        while not self._stop_event.wait(timeout=self.interval_seconds):
            self.run_once()
