import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from clipforge.db.models import AppSetting, ProcessingJob
from clipforge.db.session import create_engine, create_session_factory
from clipforge.queue.errors import (
    ActiveJobExists,
    Cancelled,
    IdempotencyConflict,
    LeaseLost,
    PermanentError,
    TransientError,
)
from clipforge.queue.repository import DAILY_JOB_CAP_KEY, LEASE_DURATION, JobQueue
from tests.clock import FakeClock
from tests.jobs import (
    Sessions,
    claim,
    event_types,
    load_job,
    make_media_asset,
    make_queued_job,
    make_video,
    new_key,
)


async def test_enqueue_is_idempotent_per_key(queue: JobQueue, sessions: Sessions) -> None:
    key = new_key()
    video_id = await make_video(sessions)
    asset_id = await make_media_asset(sessions, video_id)

    first, created = await queue.enqueue(video_id, key, media_asset_id=asset_id)
    again, created_again = await queue.enqueue(video_id, key, media_asset_id=asset_id)

    assert (created, created_again) == (True, False)
    assert again.id == first.id
    assert (first.status, first.stage) == ("queued", "queued")
    assert await event_types(sessions, first.id) == ["created"]


async def test_enqueue_rejects_reused_key_for_another_video(
    queue: JobQueue, sessions: Sessions
) -> None:
    key = new_key()
    await queue.enqueue(await make_video(sessions), key)

    with pytest.raises(IdempotencyConflict):
        await queue.enqueue(await make_video(sessions), key)


async def test_new_key_while_a_job_is_active_is_rejected(
    queue: JobQueue, sessions: Sessions
) -> None:
    key = new_key()
    video_id = await make_video(sessions)
    await queue.enqueue(video_id, key)

    with pytest.raises(ActiveJobExists):
        await queue.enqueue(video_id, new_key())


async def test_job_without_ready_source_awaits_it_and_is_not_claimed(
    queue: JobQueue, sessions: Sessions
) -> None:
    key = new_key()
    job, _ = await queue.enqueue(await make_video(sessions), key)

    assert (job.status, job.stage) == ("awaiting_source", "awaiting_source")
    assert await queue.claim_next("worker-a") is None


async def test_claim_sets_a_new_lease(
    queue: JobQueue, sessions: Sessions, clock: FakeClock
) -> None:
    job_id = await make_queued_job(queue, sessions)

    claimed = await claim(queue)

    job = await load_job(sessions, job_id)
    assert claimed.lease.job_id == job_id
    assert (job.status, job.attempts, job.leased_by) == ("running", 1, "worker-a")
    assert job.lease_token == claimed.lease.token
    assert job.lease_version == claimed.lease.version == 1
    assert job.lease_expires_at == claimed.lease.expires_at == clock() + LEASE_DURATION
    assert job.started_at == clock()
    assert await queue.claim_next("worker-b") is None


async def test_two_concurrent_claimers_never_get_the_same_job(
    migrated_database_url: str, sessions: Sessions, clock: FakeClock
) -> None:
    other_engine = create_engine(migrated_database_url, pool_size=1)
    try:
        queue_a = JobQueue(sessions, clock=clock)
        queue_b = JobQueue(create_session_factory(other_engine), clock=clock)
        job_ids = {await make_queued_job(queue_a, sessions) for _ in range(6)}

        claimed: list[uuid.UUID] = []
        while True:
            results = await asyncio.gather(
                queue_a.claim_next("worker-a"), queue_b.claim_next("worker-b")
            )
            batch = [result.lease.job_id for result in results if result is not None]
            if not batch:
                break
            claimed.extend(batch)
    finally:
        await other_engine.dispose()

    assert len(claimed) == len(set(claimed))
    assert set(claimed) == job_ids


async def test_claim_skips_a_job_locked_by_another_connection(
    queue: JobQueue, sessions: Sessions
) -> None:
    first = await make_queued_job(queue, sessions)
    second = await make_queued_job(queue, sessions)

    async with sessions.begin() as other:
        await other.execute(
            select(ProcessingJob.id).where(ProcessingJob.id == first).with_for_update()
        )
        claimed = await claim(queue)

    assert claimed.lease.job_id == second


async def test_expired_lease_is_reclaimed_and_stale_writes_change_nothing(
    queue: JobQueue, sessions: Sessions, clock: FakeClock
) -> None:
    job_id = await make_queued_job(queue, sessions)
    stale = await claim(queue, "worker-a")

    clock.advance(LEASE_DURATION - timedelta(seconds=1))
    assert await queue.claim_next("worker-b") is None

    clock.advance(timedelta(seconds=2))
    fresh = await claim(queue, "worker-b")
    assert fresh.lease.job_id == job_id
    assert fresh.lease.version == stale.lease.version + 1
    assert fresh.attempt == 2
    before = await load_job(sessions, job_id)

    with pytest.raises(LeaseLost):
        await queue.fenced_update(stale.lease, last_error="stale write")
    with pytest.raises(LeaseLost):
        await queue.set_stage(stale.lease, "ingesting")
    with pytest.raises(LeaseLost):
        await queue.renew(stale.lease)
    with pytest.raises(LeaseLost):
        await queue.complete(stale.lease)
    with pytest.raises(LeaseLost):
        await queue.handle_error(stale.lease, TransientError("stale"))

    after = await load_job(sessions, job_id)
    assert (after.status, after.stage, after.last_error) == ("running", "queued", None)
    assert after.lease_token == fresh.lease.token
    assert after.updated_at == before.updated_at
    assert await event_types(sessions, job_id) == ["created", "claimed", "reclaimed"]


async def test_renew_extends_the_lease(
    queue: JobQueue, sessions: Sessions, clock: FakeClock
) -> None:
    await make_queued_job(queue, sessions)
    claimed = await claim(queue)

    clock.advance(timedelta(minutes=4))
    renewed = await queue.renew(claimed.lease)
    clock.advance(timedelta(minutes=4))

    assert renewed.expires_at == clock() - timedelta(minutes=4) + LEASE_DURATION
    assert await queue.claim_next("worker-b") is None
    await queue.set_stage(renewed, "ingesting")


async def test_job_advances_through_stages_and_completes(
    queue: JobQueue, sessions: Sessions
) -> None:
    job_id = await make_queued_job(queue, sessions)
    claimed = await claim(queue)

    await queue.set_stage(claimed.lease, "ingesting")
    await queue.set_stage(claimed.lease, "validated")
    await queue.complete(claimed.lease)

    job = await load_job(sessions, job_id)
    assert (job.status, job.stage) == ("completed", "completed")
    assert (job.lease_token, job.lease_expires_at, job.leased_by) == (None, None, None)
    assert job.finished_at is not None
    assert await event_types(sessions, job_id) == [
        "created",
        "claimed",
        "stage_changed",
        "stage_changed",
        "completed",
    ]


async def test_complete_without_clips(queue: JobQueue, sessions: Sessions) -> None:
    job_id = await make_queued_job(queue, sessions)
    claimed = await claim(queue)

    await queue.complete(claimed.lease, no_clips=True)

    job = await load_job(sessions, job_id)
    assert (job.status, job.stage) == ("completed_no_clips", "completed_no_clips")


async def test_permanent_error_fails_without_retry(queue: JobQueue, sessions: Sessions) -> None:
    job_id = await make_queued_job(queue, sessions)
    claimed = await claim(queue)
    await queue.set_stage(claimed.lease, "ingesting")

    status = await queue.handle_error(claimed.lease, PermanentError("no audio stream"))

    job = await load_job(sessions, job_id)
    assert status == "failed"
    assert (job.status, job.stage, job.attempts) == ("failed", "ingesting", 1)
    assert job.last_error == "no audio stream"
    assert job.lease_token is None
    assert await queue.claim_next("worker-a") is None
    assert (await event_types(sessions, job_id))[-1] == "failed"


async def test_transient_errors_retry_with_backoff_until_max_attempts(
    queue: JobQueue, sessions: Sessions, clock: FakeClock
) -> None:
    job_id = await make_queued_job(queue, sessions)

    for attempt, delay_minutes in ((1, 1), (2, 5)):
        claimed = await claim(queue)
        assert claimed.attempt == attempt
        status = await queue.handle_error(claimed.lease, TransientError("timeout"))

        job = await load_job(sessions, job_id)
        assert status == "retry_scheduled"
        assert job.status == "retry_scheduled"
        assert job.next_attempt_at == clock() + timedelta(minutes=delay_minutes)
        assert job.lease_token is None

        clock.advance(timedelta(minutes=delay_minutes) - timedelta(seconds=1))
        assert await queue.claim_next("worker-a") is None
        clock.advance(timedelta(seconds=1))

    last = await claim(queue)
    assert last.attempt == 3
    status = await queue.handle_error(last.lease, TransientError("timeout"))

    job = await load_job(sessions, job_id)
    assert status == "failed"
    assert (job.status, job.attempts) == ("failed", 3)
    assert await event_types(sessions, job_id) == [
        "created",
        "claimed",
        "retry_scheduled",
        "claimed",
        "retry_scheduled",
        "claimed",
        "failed",
    ]


async def test_retry_delay_includes_jitter(sessions: Sessions, clock: FakeClock) -> None:
    queue = JobQueue(sessions, clock=clock, jitter=lambda: 0.5)
    job_id = await make_queued_job(queue, sessions)
    claimed = await claim(queue)

    await queue.handle_error(claimed.lease, TransientError("rate limited"))

    job = await load_job(sessions, job_id)
    assert job.next_attempt_at == clock() + timedelta(minutes=1, seconds=6)


async def test_unknown_errors_are_retried_like_transient_ones(
    queue: JobQueue, sessions: Sessions
) -> None:
    await make_queued_job(queue, sessions)
    claimed = await claim(queue)

    assert await queue.handle_error(claimed.lease, RuntimeError("boom")) == "retry_scheduled"


async def test_cancelled_error_ends_job_cancelled(queue: JobQueue, sessions: Sessions) -> None:
    job_id = await make_queued_job(queue, sessions)
    claimed = await claim(queue)

    assert await queue.handle_error(claimed.lease, Cancelled("requested")) == "cancelled"

    job = await load_job(sessions, job_id)
    assert (job.status, job.lease_token) == ("cancelled", None)
    assert job.finished_at is not None
    assert (await event_types(sessions, job_id))[-1] == "cancelled"


async def test_expired_lease_on_last_attempt_fails_instead_of_reclaiming(
    queue: JobQueue, sessions: Sessions, clock: FakeClock
) -> None:
    job_id = await make_queued_job(queue, sessions)
    for _ in range(3):
        await claim(queue)
        clock.advance(LEASE_DURATION + timedelta(seconds=1))

    assert await queue.claim_next("worker-b") is None

    job = await load_job(sessions, job_id)
    assert (job.status, job.attempts, job.lease_token) == ("failed", 3, None)
    assert (await event_types(sessions, job_id))[-1] == "failed"


async def test_last_error_is_redacted_and_bounded(queue: JobQueue, sessions: Sessions) -> None:
    job_id = await make_queued_job(queue, sessions)
    claimed = await claim(queue)

    await queue.handle_error(
        claimed.lease, PermanentError("GET https://x.test/a?key=secret " + "x" * 5000)
    )

    job = await load_job(sessions, job_id)
    assert job.last_error is not None
    assert "secret" not in job.last_error
    assert len(job.last_error) <= 2000


async def test_attaching_a_source_queues_waiting_jobs(
    queue: JobQueue, sessions: Sessions, clock: FakeClock
) -> None:
    video_id = await make_video(sessions)
    job, _ = await queue.enqueue(video_id, new_key())
    asset_id = await make_media_asset(sessions, video_id)

    assert await queue.attach_source(video_id, asset_id) == 1

    queued = await load_job(sessions, job.id)
    assert (queued.status, queued.stage, queued.media_asset_id) == ("queued", "queued", asset_id)
    assert (await claim(queue)).media_asset_id == asset_id
    assert await queue.attach_source(video_id, asset_id) == 0
    assert (await event_types(sessions, job.id))[:2] == ["created", "source_attached"]


@pytest.fixture
async def daily_cap(sessions: Sessions, clock: FakeClock) -> AsyncIterator[None]:
    """A cap of 2 jobs per 24 h, on a clock far from jobs that other tests started."""
    clock.advance(timedelta(days=3650))
    async with sessions.begin() as session:
        await session.execute(
            insert(AppSetting)
            .values(key=DAILY_JOB_CAP_KEY, value=2)
            .on_conflict_do_update(index_elements=["key"], set_={"value": 2})
        )
    yield
    async with sessions.begin() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key == DAILY_JOB_CAP_KEY))


@pytest.mark.usefixtures("daily_cap")
async def test_daily_cap_holds_new_jobs_but_lets_started_ones_finish(
    queue: JobQueue, sessions: Sessions, clock: FakeClock
) -> None:
    job_ids = [await make_queued_job(queue, sessions) for _ in range(3)]
    done, retrying = await claim(queue), await claim(queue)
    await queue.complete(done.lease)
    await queue.schedule_retry(retrying.lease, TransientError("timeout"))
    [waiting] = set(job_ids) - {done.lease.job_id, retrying.lease.job_id}

    clock.advance(timedelta(hours=1))
    assert (await claim(queue)).lease.job_id == retrying.lease.job_id, "started jobs may retry"
    assert await queue.claim_next("worker-a") is None
    assert (await load_job(sessions, waiting)).status == "queued"

    clock.advance(timedelta(hours=23))
    assert (await claim(queue)).lease.job_id == waiting
