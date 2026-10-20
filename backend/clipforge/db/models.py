import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    CHAR,
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonObject = dict[str, Any]


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: Uuid,
        datetime: DateTime(timezone=True),
        str: Text,
        int: Integer,
        JsonObject: JSONB,
    }


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=func.gen_random_uuid())


def created_at() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def updated_at() -> Mapped[datetime]:
    return mapped_column(server_default=func.now(), onupdate=func.now())


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = uuid_pk()
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str | None]
    active: Mapped[bool] = mapped_column(server_default="true")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class YoutubeSource(Base):
    __tablename__ = "youtube_sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str]
    name: Mapped[str]
    channel_id: Mapped[str | None] = mapped_column(unique=True)
    region_code: Mapped[str | None] = mapped_column(String(2))
    enabled: Mapped[bool] = mapped_column(server_default="true")
    last_run_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = uuid_pk()
    youtube_video_id: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str]
    description: Mapped[str | None]
    channel_id: Mapped[str]
    channel_title: Mapped[str | None]
    category_id: Mapped[str | None]
    duration_seconds: Mapped[int | None]
    published_at: Mapped[datetime | None]
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    like_count: Mapped[int | None] = mapped_column(BigInteger)
    comment_count: Mapped[int | None] = mapped_column(BigInteger)
    default_language: Mapped[str | None]
    thumbnail_url: Mapped[str | None]
    eligibility: Mapped[str] = mapped_column(server_default="pending")
    ineligibility_reason: Mapped[str | None]
    rights_status: Mapped[str] = mapped_column(server_default="unverified")
    discovered_at: Mapped[datetime] = created_at()
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class VideoSource(Base):
    __tablename__ = "video_sources"

    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("youtube_sources.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[str | None]
    first_seen_at: Mapped[datetime] = created_at()
    last_seen_at: Mapped[datetime] = created_at()


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    kind: Mapped[str]
    source_url: Mapped[str | None]
    original_filename: Mapped[str | None]
    local_path: Mapped[str | None]
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    sha256: Mapped[str | None] = mapped_column(CHAR(64))
    permission_evidence: Mapped[JsonObject]
    state: Mapped[str] = mapped_column(server_default="pending")
    media_info: Mapped[JsonObject | None]
    failure_reason: Mapped[str | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("videos.id", ondelete="RESTRICT"))
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(server_default="queued")
    stage: Mapped[str] = mapped_column(server_default="queued")
    attempts: Mapped[int] = mapped_column(server_default="0")
    max_attempts: Mapped[int] = mapped_column(server_default="3")
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    lease_token: Mapped[uuid.UUID | None]
    lease_version: Mapped[int] = mapped_column(BigInteger, server_default="0")
    lease_expires_at: Mapped[datetime | None]
    leased_by: Mapped[str | None]
    settings_snapshot: Mapped[JsonObject] = mapped_column(server_default="{}")
    cancel_requested: Mapped[bool] = mapped_column(server_default="false")
    last_error: Mapped[str | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_jobs.id", ondelete="RESTRICT"))
    event_type: Mapped[str]
    stage: Mapped[str | None]
    status: Mapped[str | None]
    attempt: Mapped[int | None]
    lease_version: Mapped[int | None] = mapped_column(BigInteger)
    message: Mapped[str | None]
    details: Mapped[JsonObject] = mapped_column(server_default="{}")
    created_at: Mapped[datetime] = created_at()


class ClipCandidate(Base):
    __tablename__ = "clip_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"))
    start_ms: Mapped[int]
    end_ms: Mapped[int]
    hook_score: Mapped[int] = mapped_column(SmallInteger)
    emotion_score: Mapped[int] = mapped_column(SmallInteger)
    clarity_score: Mapped[int] = mapped_column(SmallInteger)
    relevance_score: Mapped[int] = mapped_column(SmallInteger)
    visual_score: Mapped[int] = mapped_column(SmallInteger)
    completeness_score: Mapped[int] = mapped_column(SmallInteger)
    total_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    title: Mapped[str | None]
    hook_line: Mapped[str | None]
    explanation: Mapped[str | None]
    transcript_excerpt: Mapped[str | None]
    selected: Mapped[bool] = mapped_column(server_default="false")
    rank: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = created_at()


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clip_candidates.id", ondelete="RESTRICT"), unique=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_jobs.id", ondelete="RESTRICT"))
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("videos.id", ondelete="RESTRICT"))
    start_ms: Mapped[int]
    end_ms: Mapped[int]
    review_status: Mapped[str] = mapped_column(server_default="pending_review")
    drive_file_id: Mapped[str | None]
    drive_web_view_link: Mapped[str | None]
    title: Mapped[str | None]
    title_override: Mapped[str | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ClipArtifact(Base):
    __tablename__ = "clip_artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"))
    kind: Mapped[str]
    local_path: Mapped[str | None]
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(CHAR(64))
    md5: Mapped[str | None] = mapped_column(CHAR(32))
    upload_state: Mapped[str] = mapped_column(server_default="pending")
    drive_resumable_uri: Mapped[str | None]
    drive_file_id: Mapped[str | None]
    local_deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ClipReview(Base):
    __tablename__ = "clip_reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"))
    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    decision: Mapped[str]
    note: Mapped[str | None]
    title_override: Mapped[str | None]
    created_at: Mapped[datetime] = created_at()


class AiProviderConfig(Base):
    __tablename__ = "ai_provider_configs"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str]
    model: Mapped[str]
    capability: Mapped[str]
    secret_ref: Mapped[str]
    enabled: Mapped[bool] = mapped_column(server_default="true")
    rpm_limit: Mapped[int | None]
    rpd_limit: Mapped[int | None]
    quota_reset: Mapped[str] = mapped_column(server_default="utc_midnight")
    price_input_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default="0")
    price_output_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default="0")
    price_per_audio_minute: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default="0")
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="USD")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ProviderUsage(Base):
    __tablename__ = "provider_usage"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    provider: Mapped[str]
    model: Mapped[str]
    capability: Mapped[str]
    success: Mapped[bool]
    error_code: Mapped[str | None]
    latency_ms: Mapped[int]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    stage: Mapped[str | None]
    audio_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default="0")
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="USD")
    created_at: Mapped[datetime] = created_at()


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class AppSettingEvent(Base):
    __tablename__ = "app_setting_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    setting_key: Mapped[str]
    old_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = created_at()


class OauthCredential(Base):
    __tablename__ = "oauth_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(unique=True)
    encrypted_refresh_token: Mapped[str]
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    account_email: Mapped[str | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
