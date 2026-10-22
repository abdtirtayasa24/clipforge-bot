"""Worker crashes, split-brain leases and database outages (ADR-005, ADR-006, ADR-014)."""

import asyncio
import contextlib
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import Stage, StageResult
from clipforge.queue.repository import LEASE_DURATION, JobQueue
from clipforge.storage.drive import DriveClient, RemoteFile
from tests.db import libpq_url
from tests.drive import FakeTokens
from tests.jobs import claim, event_types, load_job
from tests.pipeline import PROPOSALS, Pipeline

PIPELINE_STAGES = [
    "validated",
    "audio_extracted",
    "transcribed",
    "analyzed",
    "candidates_selected",
    "rendering",
    "uploading",
    "affiliate_matching",
]
PAST_LEASE = LEASE_DURATION + timedelta(seconds=1)


class KillAfterWork:
    """Does the stage's work, then hangs as if killed before the stage could be recorded."""

    def __init__(self, stage: Stage) -> None:
        self.name = stage.name
        self.running_name = stage.running_name
        self._stage = stage
        self.killed = asyncio.Event()

    async def run(self, ctx: StageContext) -> StageResult:
        if self.killed.is_set():
            return await self._stage.run(ctx)
        await self._stage.run(ctx)
        self.killed.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def replace_stage(stages: list[Stage], name: str, make: type[KillAfterWork]) -> KillAfterWork:
    index = [stage.name for stage in stages].index(name)
    wrapper = make(stages[index])
    stages[index] = wrapper
    return wrapper


@pytest.mark.parametrize("stage_name", PIPELINE_STAGES)
async def test_kill_9_after_any_stage_resumes_without_duplicates(
    pipeline: Pipeline, stage_name: str
) -> None:
    job_id = await pipeline.enqueue()
    stages = pipeline.stages()
    killer = replace_stage(stages, stage_name, KillAfterWork)

    first = asyncio.create_task(pipeline.runner(stages).run(await claim(pipeline.queue)))
    await killer.killed.wait()
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    assert (await load_job(pipeline.sessions, job_id)).status == "running"
    assert await pipeline.queue.claim_next("worker-b") is None, "the lease still holds"

    pipeline.clock.advance(PAST_LEASE)
    status = await pipeline.runner(stages).run(await claim(pipeline.queue, "worker-b"))

    assert status == "completed"
    assert "reclaimed" in await event_types(pipeline.sessions, job_id)
    await pipeline.assert_recovered(job_id)
    # Transcribed chunks are verified artifacts: the reclaimed attempt pays for none twice.
    calls = pipeline.transcription.calls
    assert len(calls) == len(set(calls))


class StallingDrive(DriveClient):
    """Freezes the worker the first time it reaches `method` during an upload."""

    def __init__(self, pipeline: Pipeline, method: str) -> None:
        super().__init__(pipeline.http, FakeTokens(), pipeline.sessions)
        self._method = method
        self.stalled = asyncio.Event()
        self.resume = asyncio.Event()

    async def _maybe_stall(self, method: str) -> None:
        if method == self._method and not self.stalled.is_set():
            self.stalled.set()
            await self.resume.wait()

    async def folder_id(self) -> str:
        await self._maybe_stall("folder_id")
        return await super().folder_id()

    async def upload(self, session_uri: str, path: Path, size: int, *, offset: int) -> RemoteFile:
        await self._maybe_stall("upload")
        return await super().upload(session_uri, path, size, offset=offset)


async def test_stale_worker_waking_mid_upload_never_uploads_twice(pipeline: Pipeline) -> None:
    job_id = await pipeline.enqueue()
    # Worker A freezes (e.g. SIGSTOP or a stalled VM) just before starting the first upload.
    drive_a = StallingDrive(pipeline, "folder_id")
    worker_a = asyncio.create_task(
        pipeline.runner(pipeline.stages(drive=drive_a)).run(await claim(pipeline.queue))
    )
    await drive_a.stalled.wait()
    # Its lease expires; worker B reclaims the job and is uploading that same clip when A wakes.
    pipeline.clock.advance(PAST_LEASE)
    drive_b = StallingDrive(pipeline, "upload")
    worker_b = asyncio.create_task(
        pipeline.runner(pipeline.stages(drive=drive_b)).run(await claim(pipeline.queue, "worker-b"))
    )
    await drive_b.stalled.wait()

    drive_a.resume.set()
    async with asyncio.timeout(30):
        status_a = await worker_a
    drive_b.resume.set()
    async with asyncio.timeout(30):
        status_b = await worker_b

    assert (status_a, status_b) == ("lease_lost", "completed")
    await pipeline.assert_recovered(job_id)


async def test_dropped_database_connections_are_replaced_mid_job(pipeline: Pipeline) -> None:
    job_id = await pipeline.enqueue()
    stages = pipeline.stages()
    index = [stage.name for stage in stages].index("audio_extracted")
    audio = stages[index]
    terminated = 0

    class DropConnections:
        name = audio.name
        running_name = audio.running_name

        async def run(self, ctx: StageContext) -> StageResult:
            nonlocal terminated
            url = pipeline.sessions.kw["bind"].url.render_as_string(hide_password=False)
            with psycopg.connect(libpq_url(url), autocommit=True) as conn:
                rows = conn.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                ).fetchall()
            terminated = len(rows)
            return await audio.run(ctx)

    stages[index] = DropConnections()

    status = await pipeline.runner(stages).run(await claim(pipeline.queue))

    assert terminated > 0
    assert status == "completed"
    await pipeline.assert_recovered(job_id)


class Outage:
    """Session factory that refuses connections while the database is `down`."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise OperationalError("connect", None, ConnectionRefusedError("database is down"))

    def __call__(self) -> AsyncSession:
        self._check()
        return self._sessions()

    def begin(self) -> object:
        self._check()
        return self._sessions.begin()


async def test_database_outage_abandons_the_attempt_and_the_job_resumes(
    pipeline: Pipeline,
) -> None:
    job_id = await pipeline.enqueue()
    outage = Outage(pipeline.sessions)
    flaky_queue = JobQueue(outage, clock=pipeline.clock, jitter=lambda: 0.0)  # type: ignore[arg-type]
    stages = pipeline.stages()
    index = [stage.name for stage in stages].index("transcribed")
    transcribe = stages[index]

    class OutageAfterWork:
        name = transcribe.name
        running_name = transcribe.running_name

        async def run(self, ctx: StageContext) -> StageResult:
            result = await transcribe.run(ctx)
            outage.down = True
            return result

    stages[index] = OutageAfterWork()
    claimed = await claim(pipeline.queue)

    # Neither the stage nor the error can be recorded: the worker process exits and systemd
    # restarts it (Restart=always); the job keeps its lease until it expires.
    with pytest.raises(OperationalError):
        await pipeline.runner(stages, queue=flaky_queue).run(claimed)
    outage.down = False
    job = await load_job(pipeline.sessions, job_id)
    assert (job.status, job.stage) == ("running", "transcribing")

    pipeline.clock.advance(PAST_LEASE)
    status = await pipeline.runner().run(await claim(pipeline.queue, "worker-b"))

    assert status == "completed"
    await pipeline.assert_recovered(job_id, clips=len(PROPOSALS))
    calls = pipeline.transcription.calls
    assert len(calls) == len(set(calls))
