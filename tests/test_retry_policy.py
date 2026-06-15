from datetime import datetime, timezone
from app.jobs.retry import FixedDelayPolicy, ExponentialBackoffPolicy


def test_fixed_delay_policy():
    policy = FixedDelayPolicy(delay_seconds=15.0, max_attempts=3)
    base = datetime.now(timezone.utc)
    
    next_run = policy.get_next_run_time(1, base)
    diff = (next_run - base).total_seconds()
    assert round(diff) == 15


def test_exponential_backoff_policy_no_jitter():
    # Test without random jitter for deterministic calculations
    policy = ExponentialBackoffPolicy(
        base_delay_seconds=5.0,
        max_delay_seconds=60.0,
        max_attempts=4,
        jitter=False,
    )
    base = datetime.now(timezone.utc)

    # 1st attempt completed -> next wait = 5 * 2^0 = 5
    n1 = policy.get_next_run_time(1, base)
    assert round((n1 - base).total_seconds()) == 5

    # 2nd attempt completed -> next wait = 5 * 2^1 = 10
    n2 = policy.get_next_run_time(2, base)
    assert round((n2 - base).total_seconds()) == 10

    # 3rd attempt completed -> next wait = 5 * 2^2 = 20
    n3 = policy.get_next_run_time(3, base)
    assert round((n3 - base).total_seconds()) == 20

    # 4th attempt completed (or beyond max capped) -> next wait capped at 60.0
    n5 = policy.get_next_run_time(5, base)
    assert round((n5 - base).total_seconds()) == 60


def test_exponential_backoff_policy_with_jitter():
    policy = ExponentialBackoffPolicy(
        base_delay_seconds=5.0,
        max_delay_seconds=60.0,
        max_attempts=3,
        jitter=True,
    )
    base = datetime.now(timezone.utc)

    # With jitter, the delay should be randomized but bounded:
    # 5.0 <= delay <= 5.0 * 2^(attempt-1)
    for attempt in range(1, 4):
        next_run = policy.get_next_run_time(attempt, base)
        delay = (next_run - base).total_seconds()
        max_allowed_delay = min(5.0 * (2 ** max(0, attempt - 1)), 60.0)
        assert 5.0 <= delay <= max_allowed_delay
