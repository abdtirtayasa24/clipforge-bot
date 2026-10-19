import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import exists, func, select

from clipforge.api.deps import DbSession
from clipforge.auth.deps import require_admin
from clipforge.db.models import MediaAsset, Video, VideoSource

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
