import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import and_, func, or_, select

from clipforge.api.deps import DbSession, JobQueueDep
from clipforge.api.errors import ApiError, Conflict, NotFound
from clipforge.api.v1.schemas.jobs import (
    JobDetail,
    JobEventOut,
    JobOut,
    JobPage,
    StageCost,
    cost_lines,
    decimal_text,
)
from clipforge.auth.deps import require_admin
from clipforge.db.models import JobEvent, MediaAsset, ProcessingJob, Video
from clipforge.queue.errors import (
    ActiveJobExists,
    IdempotencyConflict,
    InvalidJobTransition,
    JobNotFound,
)

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_admin)])

MAX_IDEMPOTENCY_KEY_LENGTH = 200
JobStatus = Literal[
    "awaiting_source",
    "queued",
    "running",
    "retry_scheduled",
    "completed",
    "completed_no_clips",
    "failed",
    "cancelled",
]


async def _usable_source(session: DbSession, video_id: uuid.UUID) -> uuid.UUID | None:
    """Newest ready upload, or a registered permitted URL the ingest stage can download."""
    return await session.scalar(
        select(MediaAsset.id)
        .where(
            MediaAsset.video_id == video_id,
            or_(
                and_(MediaAsset.kind == "upload", MediaAsset.state == "ready"),
                and_(MediaAsset.kind == "url", MediaAsset.state.in_(("pending", "ready"))),
            ),
        )
        .order_by(MediaAsset.created_at.desc())
        .limit(1)
    )


@router.post("/videos/{video_id}/jobs", status_code=status.HTTP_201_CREATED)
async def queue_job(
    video_id: uuid.UUID,
    response: Response,
    session: DbSession,
    queue: JobQueueDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobOut:
    if not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ApiError(
            f"an Idempotency-Key header of 1-{MAX_IDEMPOTENCY_KEY_LENGTH} characters is required"
        )
    if await session.get(Video, video_id) is None:
        raise NotFound("video not found")
    media_asset_id = await _usable_source(session, video_id)
    try:
        job, created = await queue.enqueue(video_id, idempotency_key, media_asset_id=media_asset_id)
    except ActiveJobExists:
        raise Conflict("the video already has an active job") from None
    except IdempotencyConflict:
        raise Conflict("the Idempotency-Key was already used for another video") from None
    if not created:
        response.status_code = status.HTTP_200_OK
    return JobOut.model_validate(job)


@router.get("/jobs")
async def list_jobs(
    session: DbSession,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    video_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobPage:
    query = select(ProcessingJob)
    if status_filter:
        query = query.where(ProcessingJob.status == status_filter)
    if video_id:
        query = query.where(ProcessingJob.video_id == video_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    jobs = await session.scalars(
        query.order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
        .limit(limit)
        .offset(offset)
    )
    return JobPage(
        items=[JobOut.model_validate(job) for job in jobs],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, session: DbSession, queue: JobQueueDep) -> JobDetail:
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        raise NotFound("job not found")
    events = await session.scalars(
        select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
    )
    costs = await queue.estimated_costs(job_id)
    stage_costs = await queue.estimated_costs_by_stage(job_id)
    return JobDetail.model_validate(
        {
            **JobOut.model_validate(job).model_dump(),
            "events": [JobEventOut.model_validate(event) for event in events],
            "costs": cost_lines(costs),
            "costs_by_stage": [
                StageCost(stage=stage, currency=currency, estimated=decimal_text(total))
                for (stage, currency), total in stage_costs.items()
            ],
        }
    )


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: uuid.UUID, queue: JobQueueDep) -> JobOut:
    try:
        job = await queue.retry(job_id)
    except JobNotFound:
        raise NotFound("job not found") from None
    except InvalidJobTransition as exc:
        raise Conflict(str(exc)) from None
    except ActiveJobExists:
        raise Conflict("the video already has an active job") from None
    return JobOut.model_validate(job)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: uuid.UUID, queue: JobQueueDep) -> JobOut:
    try:
        job = await queue.request_cancel(job_id)
    except JobNotFound:
        raise NotFound("job not found") from None
    except InvalidJobTransition as exc:
        raise Conflict(str(exc)) from None
    return JobOut.model_validate(job)
