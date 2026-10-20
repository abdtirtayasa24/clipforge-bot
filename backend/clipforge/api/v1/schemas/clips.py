import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from clipforge.api.v1.schemas.jobs import CostOut

ReviewStatus = Literal["pending_review", "approved", "rejected"]
ReviewDecision = Literal["approved", "rejected"]
TitleOverride = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class ClipScores(BaseModel):
    hook: int
    emotion: int
    clarity: int
    relevance: int
    visual: int
    completeness: int
    total: float


class ClipArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    size_bytes: int | None
    upload_state: str
    drive_file_id: str | None
    created_at: datetime


class ClipReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admin_user_id: uuid.UUID
    decision: ReviewDecision
    note: str | None
    title_override: str | None
    created_at: datetime


class ClipOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    video_id: uuid.UUID
    video_title: str
    start_ms: int
    end_ms: int
    review_status: ReviewStatus
    title: str | None
    title_override: str | None
    effective_title: str | None
    total_score: float
    rank: int | None
    drive_file_id: str | None
    drive_web_view_link: str | None
    created_at: datetime
    updated_at: datetime


class CaptionLinkOut(BaseModel):
    name: str
    merchant: str
    url: str
    is_primary: bool


class CaptionOut(BaseModel):
    """Copyable draft; links are approved ones only, and none while affiliate links are off."""

    text: str
    links: list[CaptionLinkOut]
    disclosure: str | None


class ClipDetail(ClipOut):
    hook_line: str | None
    explanation: str | None
    transcript_excerpt: str | None
    scores: ClipScores
    artifacts: list[ClipArtifactOut]
    reviews: list[ClipReviewOut]
    job_costs: list[CostOut]
    caption: CaptionOut


class ClipPage(BaseModel):
    items: list[ClipOut]
    total: int
    limit: int
    offset: int


class ClipReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    note: str | None = Field(default=None, max_length=2000)
    title_override: TitleOverride | None = None
