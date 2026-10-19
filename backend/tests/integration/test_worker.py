import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, update

from clipforge.db.models import ProcessingJob
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.runner import PipelineRunner
from clipforge.pipeline.stage import StageResult, verified_artifact
from clipforge.queue.errors import PermanentError, TransientError
from clipforge.queue.repository import LEASE_DURATION, JobQueue
from clipforge.workers import main as worker_main
from clipforge.workers.main import Worker
from tests.clock import FakeClock
from tests.jobs import Sessions, claim, event_types, load_job, make_queued_job

Action = Callable[[StageContext], Awaitable[None]]


class FakeStage:
    def __init__(
        self,
        name: str,
        running_name: str | None = None,
        action: Action | None = None,
        result: StageResult | None = None,
    ) -> None:
        self.name = name
        self.running_name = running_name
        self.action = action
        self.result = result or StageResult()
        self.calls = 0

    async def run(self, ctx: StageContext) -> StageResult:
        self.calls += 1
        if self.action is not None:
            await self.action(ctx)
        return self.result


def three_stages(middle: Action | None = None) -> list[FakeStage]:
    return [
        FakeStage("validated", running_name="ingesting"),
        FakeStage("audio_extracted", action=middle),
        FakeStage("transcribed"),
    ]


def runner_for(
    queue: JobQueue, stages: list[FakeStage], tmp_path: Path, heartbeat_interval: float = 60.0
) -> PipelineRunner:
    return PipelineRunner(
        queue, stages, workspace_root=tmp_path, heartbeat_interval=heartbeat_interval
    )


async def request_cancel(sessions: Sessions, job_id: uuid.UUID) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(ProcessingJob).where(ProcessingJob.id == job_id).values(cancel_requested=True)
        )


async def wait_for(condition: Callable[[], Awaitable[bool]]) -> None:
    for _ in range(500):
        if await condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within 5 s")


async def test_job_runs_through_all_stages_and_completes(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    stages = three_stages()

    status = await runner_for(queue, stages, tmp_path).run(await claim(queue))

    job = await load_job(sessions, job_id)
    assert status == "completed"
    assert (job.status, job.stage) == ("completed", "completed")
    assert [stage.calls for stage in stages] == [1, 1, 1]
    assert await event_types(sessions, job_id) == [
        "created",
        "claimed",
        "stage_changed",
        "stage_changed",
        "stage_changed",
        "stage_changed",
        "completed",
    ]


async def test_stages_get_a_private_job_workspace(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    seen: list[Path] = []

    async def record(ctx: StageContext) -> None:
        seen.append(ctx.workspace)

    await runner_for(queue, three_stages(record), tmp_path).run(await claim(queue))

    assert seen == [tmp_path / "jobs" / str(job_id)]
    assert seen[0].is_dir()


async def test_killed_worker_resumes_from_last_completed_stage(
    queue: JobQueue, sessions: Sessions, clock: FakeClock, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    started = asyncio.Event()

    async def hang(_: StageContext) -> None:
        started.set()
        await asyncio.Event().wait()

    stages = three_stages(hang)
    first = asyncio.create_task(runner_for(queue, stages, tmp_path).run(await claim(queue)))
    await started.wait()
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first

    job = await load_job(sessions, job_id)
    assert (job.status, job.stage) == ("running", "validated")

    clock.advance(LEASE_DURATION + timedelta(seconds=1))
    stages[1].action = None
    status = await runner_for(queue, stages, tmp_path).run(await claim(queue, "worker-b"))

    assert status == "completed"
    assert [stage.calls for stage in stages] == [1, 2, 1]


async def test_cancellation_between_stages_stops_the_job(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    stages = three_stages()

    async def cancel(ctx: StageContext) -> None:
        await request_cancel(sessions, ctx.job.lease.job_id)

    stages[0].action = cancel
    status = await runner_for(queue, stages, tmp_path).run(await claim(queue))

    job = await load_job(sessions, job_id)
    assert status == "cancelled"
    assert (job.status, job.stage) == ("cancelled", "validated")
    assert [stage.calls for stage in stages] == [1, 0, 0]
    assert (await event_types(sessions, job_id))[-1] == "cancelled"


async def test_cancellation_within_a_stage_stops_at_its_checkpoint(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    reached_after_checkpoint = False

    async def cancel_then_check(ctx: StageContext) -> None:
        nonlocal reached_after_checkpoint
        await request_cancel(sessions, ctx.job.lease.job_id)
        await ctx.check_cancelled()
        reached_after_checkpoint = True

    status = await runner_for(queue, three_stages(cancel_then_check), tmp_path).run(
        await claim(queue)
    )

    job = await load_job(sessions, job_id)
    assert status == "cancelled"
    assert (job.status, job.stage) == ("cancelled", "validated")
    assert not reached_after_checkpoint


async def test_cancel_requested_before_claim_runs_no_stage(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    await request_cancel(sessions, job_id)
    stages = three_stages()

    status = await runner_for(queue, stages, tmp_path).run(await claim(queue))

    assert status == "cancelled"
    assert [stage.calls for stage in stages] == [0, 0, 0]


@pytest.mark.parametrize(
    ("error", "expected"),
    [(TransientError("timeout"), "retry_scheduled"), (PermanentError("no audio"), "failed")],
)
async def test_stage_errors_follow_the_retry_policy(
    queue: JobQueue, sessions: Sessions, tmp_path: Path, error: Exception, expected: str
) -> None:
    job_id = await make_queued_job(queue, sessions)

    async def raise_error(_: StageContext) -> None:
        raise error

    status = await runner_for(queue, three_stages(raise_error), tmp_path).run(await claim(queue))

    job = await load_job(sessions, job_id)
    assert status == expected
    assert (job.status, job.stage) == (expected, "validated")


async def test_stage_result_without_clips_finishes_early(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    stages = three_stages()
    stages[1].result = StageResult(no_clips=True)

    status = await runner_for(queue, stages, tmp_path).run(await claim(queue))

    job = await load_job(sessions, job_id)
    assert status == "completed_no_clips"
    assert job.status == "completed_no_clips"
    assert stages[2].calls == 0


async def test_heartbeat_renews_the_lease(
    queue: JobQueue, sessions: Sessions, clock: FakeClock, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)

    async def outlive_first_lease(_: StageContext) -> None:
        clock.advance(timedelta(minutes=4))
        expected = clock() + LEASE_DURATION

        async def renewed() -> bool:
            return (await load_job(sessions, job_id)).lease_expires_at == expected

        await wait_for(renewed)

    status = await runner_for(
        queue, three_stages(outlive_first_lease), tmp_path, heartbeat_interval=0.01
    ).run(await claim(queue))

    assert status == "completed"


async def test_lost_lease_cancels_the_running_stage(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    started = asyncio.Event()
    stage_cancelled = False

    async def hang(_: StageContext) -> None:
        nonlocal stage_cancelled
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stage_cancelled = True
            raise

    run = asyncio.create_task(
        runner_for(queue, three_stages(hang), tmp_path, heartbeat_interval=0.01).run(
            await claim(queue)
        )
    )
    await started.wait()
    async with sessions.begin() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(
                lease_token=func.gen_random_uuid(), lease_version=ProcessingJob.lease_version + 1
            )
        )

    async with asyncio.timeout(5):
        status = await run

    assert status == "lease_lost"
    assert stage_cancelled
    assert (await load_job(sessions, job_id)).status == "running"
    assert (await event_types(sessions, job_id))[-1] == "stage_changed"


async def test_worker_processes_queued_jobs_until_stopped(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_ids = [await make_queued_job(queue, sessions) for _ in range(2)]
    stop = asyncio.Event()
    worker = Worker(queue, runner_for(queue, three_stages(), tmp_path), "worker-a", idle_min=0.01)
    task = asyncio.create_task(worker.run(stop))

    async def all_completed() -> bool:
        jobs = [await load_job(sessions, job_id) for job_id in job_ids]
        return all(job.status == "completed" for job in jobs)

    await wait_for(all_completed)
    stop.set()
    async with asyncio.timeout(5):
        await task


async def test_stopping_mid_stage_aborts_without_releasing_the_lease(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    started = asyncio.Event()

    async def hang(_: StageContext) -> None:
        started.set()
        await asyncio.Event().wait()

    stop = asyncio.Event()
    worker = Worker(queue, runner_for(queue, three_stages(hang), tmp_path), "worker-a")
    task = asyncio.create_task(worker.run(stop))
    await started.wait()
    stop.set()
    async with asyncio.timeout(5):
        await task

    job = await load_job(sessions, job_id)
    assert (job.status, job.stage) == ("running", "validated")
    assert job.lease_token is not None


def test_idle_delay_doubles_up_to_the_maximum() -> None:
    assert worker_main.next_idle_delay(1.0, maximum=30.0) == 2.0
    assert worker_main.next_idle_delay(20.0, maximum=30.0) == 30.0


def test_main_without_database_url_exits_with_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLIPFORGE_DATABASE_URL", raising=False)

    assert worker_main.main() == 2


async def test_verified_artifact_checks_existence_and_checksum(tmp_path: Path) -> None:
    artifact = tmp_path / "audio.flac"
    digest = "a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e"

    assert not await verified_artifact(artifact, digest)
    artifact.write_bytes(b"Hello World")
    assert await verified_artifact(artifact, digest)
    assert not await verified_artifact(artifact, "0" * 64)
    assert not await verified_artifact(artifact, None)
