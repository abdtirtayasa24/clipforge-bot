from datetime import datetime


class PermanentError(Exception):
    """Rights, validation or unsupported media: the job fails and is never retried (ADR-007)."""


class TransientError(Exception):
    """Network, 5xx, timeout or rate limit: the job is retried with backoff (ADR-007)."""


class LeaseLost(Exception):
    """Another worker owns the job now; the stage aborts without writing (ADR-005)."""


class Cancelled(Exception):
    """The admin requested cancellation; the job ends `cancelled`."""


class IdempotencyConflict(Exception):
    """The idempotency key already belongs to a job for another video."""


class ActiveJobExists(Exception):
    """The video already has an unfinished job."""


class JobNotFound(Exception):
    pass


class InvalidJobTransition(Exception):
    """The requested admin action does not apply to the job's current status."""


class QuotaDeferred(Exception):
    """A provider's quota is used up: the job waits until `until` without using an attempt."""

    def __init__(self, message: str, *, until: datetime) -> None:
        super().__init__(message)
        self.until = until
