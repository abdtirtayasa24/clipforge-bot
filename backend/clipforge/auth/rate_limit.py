import time
from collections import deque
from collections.abc import Callable


class FailedAttemptLimiter:
    """In-process sliding window of failed attempts per client key (single API process)."""

    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        failures = self._failures.get(key, deque())
        cutoff = self._clock() - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._failures.pop(key, None)
        return failures

    def is_blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self._max_failures

    def record_failure(self, key: str) -> None:
        failures = self._recent(key)
        failures.append(self._clock())
        self._failures[key] = failures
