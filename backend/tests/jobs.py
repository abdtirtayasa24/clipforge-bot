import uuid

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.models import JobEvent, MediaAsset, ProcessingJob, Video
from clipforge.queue.repository import ClaimedJob, JobQueue

Sessions = async_sessionmaker[AsyncSession]


def new_key() -> str:
    return uuid.uuid4().hex


async def make_video(sessions: Sessions) -> uuid.UUID:
    async with sessions.begin() as session:
        video_id = await session.scalar(
            insert(Video)
            .values(youtube_video_id=uuid.uuid4().hex[:11], title="t", channel_id="c")
            .returning(Video.id)
        )
    assert video_id is not None
    return video_id


async def make_media_asset(sessions: Sessions, video_id: uuid.UUID) -> uuid.UUID:
    async with sessions.begin() as session:
        asset_id = await session.scalar(
            insert(MediaAsset)
            .values(video_id=video_id, kind="upload", permission_evidence={}, state="ready")
            .returning(MediaAsset.id)
        )
    assert asset_id is not None
    return asset_id


async def make_queued_job(queue: JobQueue, sessions: Sessions) -> uuid.UUID:
    video_id = await make_video(sessions)
    asset_id = await make_media_asset(sessions, video_id)
    job, _ = await queue.enqueue(video_id, new_key(), media_asset_id=asset_id)
    return job.id


async def load_job(sessions: Sessions, job_id: uuid.UUID) -> ProcessingJob:
    async with sessions() as session:
        job = await session.get(ProcessingJob, job_id)
    assert job is not None
    return job


async def event_types(sessions: Sessions, job_id: uuid.UUID) -> list[str]:
    async with sessions() as session:
        rows = await session.scalars(
            select(JobEvent.event_type).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
        )
        return list(rows)


async def claim(queue: JobQueue, worker_id: str = "worker-a") -> ClaimedJob:
    claimed = await queue.claim_next(worker_id)
    assert claimed is not None
    return claimed


async def stage_events(sessions: Sessions, job_id: uuid.UUID) -> list[str | None]:
    """Stages recorded by `stage_changed` events, in order."""
    async with sessions() as session:
        rows = await session.scalars(
            select(JobEvent.stage)
            .where(JobEvent.job_id == job_id, JobEvent.event_type == "stage_changed")
            .order_by(JobEvent.id)
        )
        return list(rows)
