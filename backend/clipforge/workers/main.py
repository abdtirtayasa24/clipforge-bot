"""Processing worker: python -m clipforge.workers.main (one job at a time)."""

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys

from clipforge.config import Settings
from clipforge.db.session import create_engine, create_session_factory
from clipforge.logging import configure_logging
from clipforge.pipeline.runner import PipelineRunner
from clipforge.pipeline.stages import default_stages
from clipforge.queue.repository import ClaimedJob, JobQueue

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_CONFIG = 0, 2
IDLE_POLL_MIN_S = 1.0
IDLE_POLL_MAX_S = 30.0


def next_idle_delay(current: float, *, maximum: float) -> float:
    return min(current * 2, maximum)


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


class Worker:
    def __init__(
        self,
        queue: JobQueue,
        runner: PipelineRunner,
        worker_id: str,
        *,
        idle_min: float = IDLE_POLL_MIN_S,
        idle_max: float = IDLE_POLL_MAX_S,
    ) -> None:
        self._queue = queue
        self._runner = runner
        self._worker_id = worker_id
        self._idle_min = idle_min
        self._idle_max = idle_max

    async def run(self, stop: asyncio.Event) -> None:
        delay = self._idle_min
        while not stop.is_set():
            try:
                claimed = await self._queue.claim_next(self._worker_id)
            except Exception:
                logger.exception("claiming a job failed")
                claimed = None
            if claimed is None:
                await _wait(stop, delay)
                delay = next_idle_delay(delay, maximum=self._idle_max)
                continue
            delay = self._idle_min
            await self._run_until_stopped(claimed, stop)

    async def _run_until_stopped(self, claimed: ClaimedJob, stop: asyncio.Event) -> None:
        # On stop the current stage is aborted and nothing is released: the lease expires and
        # the job is reclaimed after a restart.
        job = asyncio.create_task(self._runner.run(claimed))
        stopping = asyncio.create_task(stop.wait())
        await asyncio.wait({job, stopping}, return_when=asyncio.FIRST_COMPLETED)
        stopping.cancel()
        if not job.done():
            logger.info(
                "stopping; aborting the current job", extra={"job_id": str(claimed.lease.job_id)}
            )
            job.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await job


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        logger.info("stop requested")
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, request_stop)
        except NotImplementedError:
            # Windows event loops cannot install signal handlers.
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(request_stop))


async def serve(settings: Settings, database_url: str) -> None:
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    engine = create_engine(
        database_url,
        pool_size=settings.db_pool_size,
        disable_prepared_statements=settings.db_disable_prepared_statements,
    )
    try:
        queue = JobQueue(create_session_factory(engine))
        runner = PipelineRunner(queue, default_stages(), workspace_root=settings.workspace_dir)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        logger.info("worker started", extra={"worker_id": worker_id})
        await Worker(queue, runner, worker_id).run(stop)
    finally:
        await engine.dispose()
    logger.info("worker stopped")


def main() -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    if settings.database_url is None:
        logger.error("CLIPFORGE_DATABASE_URL is not set")
        return EXIT_CONFIG
    # psycopg's async mode cannot run on Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(
        serve(settings, settings.database_url.get_secret_value()), loop_factory=loop_factory
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
