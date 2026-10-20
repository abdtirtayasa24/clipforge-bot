import asyncio
import contextlib
import logging
import uuid
from collections.abc import Sequence
from pathlib import Path

from clipforge.logging import log_context
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import Stage
from clipforge.pipeline.stages.notify import Notifier
from clipforge.queue.errors import LeaseLost
from clipforge.queue.repository import ClaimedJob, JobQueue, Lease
from clipforge.storage.drive_oauth import DriveAuthExpired, DriveNotConnected

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 60.0
LEASE_LOST = "lease_lost"


class PipelineRunner:
    def __init__(
        self,
        queue: JobQueue,
        stages: Sequence[Stage],
        *,
        workspace_root: Path,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_S,
        notifier: Notifier | None = None,
    ) -> None:
        if not stages:
            raise ValueError("a pipeline needs at least one stage")
        self._queue = queue
        self._stages = list(stages)
        self._workspace_root = workspace_root
        self._heartbeat_interval = heartbeat_interval
        self._notifier = notifier

    def _remaining_stages(self, current: str) -> list[Stage]:
        """Stages still to run for a job whose recorded stage is `current`."""
        for index, stage in enumerate(self._stages):
            if current == stage.name:
                return self._stages[index + 1 :]
            if current == stage.running_name:
                return self._stages[index:]
        return self._stages

    async def run(self, job: ClaimedJob) -> str:
        """Runs the job's remaining stages and returns its resulting status.

        Cancelling the calling task aborts the current stage without writing anything, so the
        lease simply expires and the job is reclaimed later.
        """
        with log_context(job_id=str(job.lease.job_id), attempt=job.attempt):
            work = asyncio.create_task(self._run_stages(job))
            heartbeat = asyncio.create_task(self._heartbeat(job.lease, work))
            try:
                return await work
            except asyncio.CancelledError:
                if heartbeat.done() and not heartbeat.cancelled() and heartbeat.result():
                    logger.warning("lease lost; stage aborted")
                    return LEASE_LOST
                work.cancel()
                raise
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat

    async def _heartbeat(self, lease: Lease, work: asyncio.Task[str]) -> bool:
        """Renews the lease until cancelled; returns True after cancelling `work` on LeaseLost."""
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self._queue.renew(lease)
            except LeaseLost:
                work.cancel()
                return True
            except Exception:
                # The lease outlives several missed renewals; keep trying.
                logger.warning("lease renewal failed", exc_info=True)

    async def _run_stages(self, job: ClaimedJob) -> str:
        lease = job.lease
        workspace = self._workspace_root / "jobs" / str(lease.job_id)
        context = StageContext(job=job, queue=self._queue, workspace=workspace)
        try:
            await asyncio.to_thread(workspace.mkdir, mode=0o700, parents=True, exist_ok=True)
            for stage in self._remaining_stages(job.stage):
                await context.check_cancelled()
                with log_context(stage=stage.name):
                    if stage.running_name:
                        await self._queue.set_stage(lease, stage.running_name)
                    result = await stage.run(context)
                    await self._queue.set_stage(lease, stage.name, details=result.details)
                if result.no_clips:
                    await self._queue.complete(lease, no_clips=True)
                    return "completed_no_clips"
            await self._queue.complete(lease)
        except LeaseLost:
            logger.warning("lease lost; stage aborted")
            return LEASE_LOST
        except Exception as error:
            try:
                status = await self._queue.handle_error(lease, error)
            except LeaseLost:
                logger.warning("lease lost while recording an error")
                return LEASE_LOST
            await self._notify_error(lease.job_id, status, error)
            return status
        if self._notifier:
            await self._notifier.clips_ready(lease.job_id)
        return "completed"

    async def _notify_error(self, job_id: uuid.UUID, status: str, error: Exception) -> None:
        if self._notifier is None:
            return
        if isinstance(error, DriveNotConnected | DriveAuthExpired):
            await self._notifier.drive_disconnected()
        if status == "failed":
            await self._notifier.job_failed(job_id)
