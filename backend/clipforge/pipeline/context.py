from dataclasses import dataclass
from pathlib import Path

from clipforge.queue.errors import Cancelled
from clipforge.queue.repository import ClaimedJob, JobQueue


@dataclass(frozen=True)
class StageContext:
    job: ClaimedJob
    queue: JobQueue
    workspace: Path

    async def check_cancelled(self) -> None:
        """Cancellation checkpoint; long stages call it between units of work."""
        if await self.queue.cancel_requested(self.job.lease.job_id):
            raise Cancelled("cancellation requested")
