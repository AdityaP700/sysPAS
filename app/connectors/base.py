import time
import threading
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.connectors.models import ConnectorRecord


class ConnectorError(Exception):
    """Base exception for all connector operations."""
    pass


class RateLimitExceededError(ConnectorError):
    """Raised when a connector's request rate limit is exceeded."""
    pass


class CircuitBreakerOpenError(ConnectorError):
    """Raised when execution is blocked due to the circuit breaker being OPEN."""
    pass


class RateLimiter:
    """Sliding window in-memory rate limiter per connector."""
    def __init__(self):
        self._lock = threading.Lock()
        self._calls: Dict[str, List[float]] = {}

    def check_limit(self, connector_id: str, limit_per_minute: int) -> None:
        if limit_per_minute <= 0:
            return  # No limit enforced

        now = time.time()
        cutoff = now - 60.0
        with self._lock:
            timestamps = self._calls.get(connector_id, [])
            timestamps = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= limit_per_minute:
                raise RateLimitExceededError(
                    f"Rate limit of {limit_per_minute} calls/min exceeded for connector '{connector_id}'."
                )
            timestamps.append(now)
            self._calls[connector_id] = timestamps


_rate_limiter = RateLimiter()


class BaseConnector(ABC):
    """Abstract base connector plugin interface for all external integrations with built-in rate-limiting and circuit breaker wrapper."""

    def __init__(self, record: ConnectorRecord, repo: Optional[Any] = None):
        self.record = record
        self.repo = repo

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Wrapper method that enforces rate limits and circuit breaker rules around subclasses' execution."""
        # 1. Enforce Rate Limit
        _rate_limiter.check_limit(self.record.connector_id, self.record.rate_limit_per_minute)

        # 2. Enforce Circuit Breaker
        self._evaluate_circuit_breaker()

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            # 3. Delegate to subclasses' execution
            result = self._execute(payload)
            
            # 4. Handle Success Transition
            self._handle_success(now_str)
            return result

        except Exception as e:
            # 5. Handle Failure Transition
            self._handle_failure(now_str, str(e))
            raise e

    @abstractmethod
    def _execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Perform the actual connector call. Implemented by subclasses."""
        pass

    @abstractmethod
    def check_health(self) -> bool:
        """Perform active connection status validation check."""
        pass

    @abstractmethod
    def validate_credentials(self) -> bool:
        """Verify that configured credentials/tokens are active and correct against target API."""
        pass

    def _evaluate_circuit_breaker(self) -> None:
        """Evaluate and transition circuit breaker states: CLOSED, OPEN, HALF_OPEN."""
        state = self.record.circuit_state
        if state == "CLOSED":
            return

        now = time.time()
        opened_at_str = self.record.circuit_opened_at
        cooldown = 30.0  # 30 seconds cooldown

        if state == "OPEN":
            if opened_at_str:
                try:
                    opened_at = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    opened_at = now
                if now - opened_at >= cooldown:
                    # Transition to HALF_OPEN to try a call
                    self.record.circuit_state = "HALF_OPEN"
                    self.record.circuit_opened_at = None
                    if self.repo:
                        self.repo.save_connector(self.record.tenant_id, self.record)
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker is OPEN for connector '{self.record.connector_id}' (cooldown active)."
                    )
            else:
                self.record.circuit_state = "HALF_OPEN"
                if self.repo:
                    self.repo.save_connector(self.record.tenant_id, self.record)

    def _handle_success(self, now_str: str) -> None:
        """Update connector state upon successful execution."""
        self.record.last_success_at = now_str
        self.record.consecutive_failures = 0
        self.record.circuit_failure_count = 0
        self.record.health_status = "HEALTHY"

        if self.record.circuit_state == "HALF_OPEN":
            self.record.circuit_state = "CLOSED"

        if self.repo:
            self.repo.save_connector(self.record.tenant_id, self.record)

    def _handle_failure(self, now_str: str, error_msg: str) -> None:
        """Update connector state and transition circuit breaker on failure."""
        self.record.consecutive_failures += 1
        self.record.circuit_failure_count += 1
        self.record.health_status = "UNHEALTHY"

        # If it was HALF_OPEN, it fails immediately back to OPEN
        if self.record.circuit_state == "HALF_OPEN" or self.record.circuit_failure_count >= 5:
            self.record.circuit_state = "OPEN"
            self.record.circuit_opened_at = now_str

        if self.repo:
            self.repo.save_connector(self.record.tenant_id, self.record)
