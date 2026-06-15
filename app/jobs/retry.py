import random
from datetime import datetime, timedelta, timezone
from typing import Optional


class FixedDelayPolicy:
    """Calculates retry times using a constant delay interval."""

    def __init__(self, delay_seconds: float = 10.0, max_attempts: int = 3):
        self.delay_seconds = delay_seconds
        self.max_attempts = max_attempts

    def get_next_run_time(self, attempt_count: int, base_time: Optional[datetime] = None) -> datetime:
        if base_time is None:
            base_time = datetime.now(timezone.utc)
        return base_time + timedelta(seconds=self.delay_seconds)


class ExponentialBackoffPolicy:
    """Calculates retry times using exponential backoff with a cap and randomized jitter."""

    def __init__(
        self,
        base_delay_seconds: float = 5.0,
        max_delay_seconds: float = 300.0,
        max_attempts: int = 3,
        jitter: bool = True,
    ):
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.max_attempts = max_attempts
        self.jitter = jitter

    def get_next_run_time(self, attempt_count: int, base_time: Optional[datetime] = None) -> datetime:
        if base_time is None:
            base_time = datetime.now(timezone.utc)
        
        # Exponential calculation: base * (2^(attempt-1))
        factor = 2 ** max(0, attempt_count - 1)
        delay = self.base_delay_seconds * factor

        if self.jitter:
            # Random jitter from base_delay up to calculated delay
            delay = random.uniform(self.base_delay_seconds, delay)

        # Cap at maximum delay threshold
        delay = min(delay, self.max_delay_seconds)
        return base_time + timedelta(seconds=delay)
