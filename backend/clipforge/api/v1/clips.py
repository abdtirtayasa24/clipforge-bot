import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, func, select

from clipforge.api.deps import DbSession, JobQueueDep
from clipforge.api.errors import NotFound
from clipforge.api.v1.schemas.clips import (
    ClipArtifactOut,
    ClipDetail,
    ClipOut,
    ClipPage,
    ClipReviewIn,
    ClipReviewOut,
    ClipScores,
    ReviewStatus,
)
from clipforge.api.v1.schemas.jobs import cost_lines
from clipforge.auth.deps import CurrentAdmin, require_admin
from clipforge.db.models import Clip, ClipArtifact, ClipCandidate, ClipReview, Video

router = APIRouter(prefix="/clips", tags=["clips"], dependencies=[Depends(require_admin)])


def _clips() -> Select[Clip, ClipCandidate, str]:
    return (
        select(Clip, ClipCandidate, Video.title)
        .join(ClipCandidate, ClipCandidate.id == Clip.candidate_id)
        .join(Video, Video.id == Clip.video_id)
    )


def _clip_fields(clip: Clip, candidate: ClipCandidate, video_title: str) -> dict[str, Any]:
    return {
        "id": clip.id,
        "job_id": clip.job_id,
        "video_id": clip.video_id,
        "video_title": video_title,
        "start_ms": clip.start_ms,
        "end_ms": clip.end_ms,
        "review_status": clip.review_status,
        "title": clip.title,
        "title_override": clip.title_override,
        "effective_title": clip.title_override or clip.title,
        "total_score": float(candidate.total_score),
        "rank": candidate.rank,
        "drive_file_id": clip.drive_file_id,
        "drive_web_view_link": clip.drive_web_view_link,
        "created_at": clip.created_at,
        "updated_at": clip.updated_at,
    }


@router.get("")
async def list_clips(
    session: DbSession,
    status_filter: Annotated[ReviewStatus | None, Query(alias="status")] = None,
    video_id: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ClipPage:
    query = _clips()
    if status_filter:
        query = query.where(Clip.review_status == status_filter)
    if video_id:
        query = query.where(Clip.video_id == video_id)
    if created_from:
        query = query.where(Clip.created_at >= created_from)
    if created_to:
        query = query.where(Clip.created_at < created_to)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.execute(
        query.order_by(Clip.created_at.desc(), Clip.id).limit(limit).offset(offset)
    )
    return ClipPage(
        items=[ClipOut.model_validate(_clip_fields(*row)) for row in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def _clip_detail(session: DbSession, queue: JobQueueDep, clip_id: uuid.UUID) -> ClipDetail:
    row = (await session.execute(_clips().where(Clip.id == clip_id))).one_or_none()
    if row is None:
        raise NotFound("clip not found")
    clip, candidate, video_title = row
    artifacts = await session.scalars(
        select(ClipArtifact)
        .where(ClipArtifact.clip_id == clip_id)
        .order_by(ClipArtifact.created_at)
    )
    reviews = await session.scalars(
        select(ClipReview)
        .where(ClipReview.clip_id == clip_id)
        .order_by(ClipReview.created_at, ClipReview.id)
    )
    return ClipDetail.model_validate(
        {
            **_clip_fields(clip, candidate, video_title),
            "hook_line": candidate.hook_line,
            "explanation": candidate.explanation,
            "transcript_excerpt": candidate.transcript_excerpt,
            "scores": ClipScores(
                hook=candidate.hook_score,
                emotion=candidate.emotion_score,
                clarity=candidate.clarity_score,
                relevance=candidate.relevance_score,
                visual=candidate.visual_score,
                completeness=candidate.completeness_score,
                total=float(candidate.total_score),
            ),
            "artifacts": [ClipArtifactOut.model_validate(artifact) for artifact in artifacts],
            "reviews": [ClipReviewOut.model_validate(review) for review in reviews],
            "job_costs": cost_lines(await queue.estimated_costs(clip.job_id)),
        }
    )


@router.get("/{clip_id}")
async def get_clip(clip_id: uuid.UUID, session: DbSession, queue: JobQueueDep) -> ClipDetail:
    return await _clip_detail(session, queue, clip_id)


@router.post("/{clip_id}/review")
async def review_clip(
    clip_id: uuid.UUID,
    body: ClipReviewIn,
    admin: CurrentAdmin,
    session: DbSession,
    queue: JobQueueDep,
) -> ClipDetail:
    """Appends an audit row; the generated title stays and an override only shadows it."""
    clip = await session.scalar(select(Clip).where(Clip.id == clip_id).with_for_update())
    if clip is None:
        raise NotFound("clip not found")
    session.add(
        ClipReview(
            clip_id=clip_id,
            admin_user_id=admin.id,
            decision=body.decision,
            note=body.note,
            title_override=body.title_override,
        )
    )
    clip.review_status = body.decision
    if body.title_override is not None:
        clip.title_override = body.title_override
    await session.commit()
    session.expire_all()
    return await _clip_detail(session, queue, clip_id)
