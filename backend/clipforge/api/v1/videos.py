import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, func, select

from clipforge.api.deps import DbSession, JobQueueDep
from clipforge.api.errors import ApiError, NotFound
from clipforge.auth.deps import CurrentAdmin, require_admin
from clipforge.db.app_settings import permitted_download_domains
from clipforge.db.models import MediaAsset, Video, VideoSource
from clipforge.ingest.evidence import PermissionEvidence
from clipforge.ingest.uploads import evidence_record
from clipforge.security.url_guard import UrlRejected, check_url

router = APIRouter(prefix="/videos", tags=["videos"], dependencies=[Depends(require_admin)])

SourceStatus = Literal["awaiting_source", "ready"]


class VideoOut(BaseModel):
    id: uuid.UUID
    youtube_video_id: str
    title: str
    channel_id: str
    channel_title: str | None
    category_id: str | None
    duration_seconds: int | None
    published_at: datetime | None
    view_count: int | None
    thumbnail_url: str | None
    eligibility: str
    rights_status: str
    source_status: SourceStatus
    discovered_at: datetime


class VideoPage(BaseModel):
    items: list[VideoOut]
    total: int
    limit: int
    offset: int


@router.get("")
async def list_videos(
    session: DbSession,
    eligibility: Literal["pending", "eligible", "ineligible"] | None = None,
    rights_status: Literal["unverified", "authorized", "rejected"] | None = None,
    source_status: SourceStatus | None = None,
    source_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VideoPage:
    has_ready_media = (
        exists()
        .where(MediaAsset.video_id == Video.id, MediaAsset.state == "ready")
        .correlate(Video)
    )
    query = select(Video, has_ready_media.label("has_ready_media"))
    if eligibility:
        query = query.where(Video.eligibility == eligibility)
    if rights_status:
        query = query.where(Video.rights_status == rights_status)
    if source_status:
        query = query.where(has_ready_media if source_status == "ready" else ~has_ready_media)
    if source_id:
        query = query.where(
            exists().where(VideoSource.video_id == Video.id, VideoSource.source_id == source_id)
        )

    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.execute(
        query.order_by(Video.discovered_at.desc(), Video.id).limit(limit).offset(offset)
    )
    items = [
        VideoOut(
            id=video.id,
            youtube_video_id=video.youtube_video_id,
            title=video.title,
            channel_id=video.channel_id,
            channel_title=video.channel_title,
            category_id=video.category_id,
            duration_seconds=video.duration_seconds,
            published_at=video.published_at,
            view_count=video.view_count,
            thumbnail_url=video.thumbnail_url,
            eligibility=video.eligibility,
            rights_status=video.rights_status,
            source_status="ready" if ready else "awaiting_source",
            discovered_at=video.discovered_at,
        )
        for video, ready in rows
    ]
    return VideoPage(items=items, total=total or 0, limit=limit, offset=offset)


class UrlRejectedError(ApiError):
    status_code = 422
    code = "url_rejected"


class MediaRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    permission: PermissionEvidence


class MediaOut(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    kind: str
    source_url: str | None
    state: str
    created_at: datetime


@router.post("/{video_id}/media", status_code=status.HTTP_201_CREATED)
async def register_media_url(
    video_id: uuid.UUID,
    body: MediaRegistration,
    session: DbSession,
    admin: CurrentAdmin,
    queue: JobQueueDep,
) -> MediaOut:
    """Registers a permitted download URL; the worker's ingest stage fetches and validates it."""
    if await session.get(Video, video_id) is None:
        raise NotFound("video not found")
    try:
        check_url(body.url, await permitted_download_domains(session))
    except UrlRejected as exc:
        raise UrlRejectedError(str(exc)) from None
    asset = MediaAsset(
        video_id=video_id,
        kind="url",
        source_url=body.url,
        permission_evidence=evidence_record(body.permission, admin.id),
        state="pending",
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    await queue.attach_source(video_id, asset.id)
    return MediaOut(
        id=asset.id,
        video_id=asset.video_id,
        kind=asset.kind,
        source_url=asset.source_url,
        state=asset.state,
        created_at=asset.created_at,
    )
