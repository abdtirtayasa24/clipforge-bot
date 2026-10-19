from datetime import timedelta

RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
MAX_JITTER_FRACTION = 0.2


def retry_delay(attempt: int, *, jitter: float) -> timedelta:
    """Delay before retrying after failed attempt `attempt` (1-based); `jitter` is in [0, 1]."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if not 0.0 <= jitter <= 1.0:
        raise ValueError("jitter must be within [0, 1]")
    base = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]
    return base + base * MAX_JITTER_FRACTION * jitter
