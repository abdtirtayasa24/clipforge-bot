"""Typed tunable settings and the per-job snapshot taken at claim (ADR-026, ADR-024)."""

from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clipforge.db.models import AppSetting
from clipforge.discovery.service import DiscoverySettings
from clipforge.queue.errors import PermanentError

QuotaReset = Literal["pacific_midnight", "utc_midnight"]
ModelRole = Literal["transcription", "analysis", "rerank", "analysis_fallback", "embedding"]


class ModelSelection(BaseModel):
    """Selected model per role; a value is `model` or `provider:model`."""

    transcription: str = "gemini-3.5-transcribe"
    analysis: str = "gemini-3.5-flash-lite"
    rerank: str = "gemini-3-flash-preview"
    analysis_fallback: str | None = "openrouter:nvidia/nemotron-3.5-super-vl:free"
    embedding: str = "gemini-embedding-2"


class SettingsSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_score: float = Field(default=75, ge=0, le=100)
    clip_min_s: float = Field(default=20, ge=5, le=180)
    clip_max_s: float = Field(default=60, ge=5, le=180)
    max_clips: int = Field(default=3, ge=1, le=3)
    max_overlap: float = Field(default=0.2, ge=0, le=1)
    audio_chunk_minutes: int = Field(default=20, ge=1, le=28)
    reserved_disk_bytes: int = Field(default=5 * 1024**3, ge=0)
    permitted_download_domains: list[str] = Field(default_factory=list)
    affiliate_links_enabled: bool = False
    affiliate_min_relevance: float = Field(default=0.7, ge=0, le=1)
    affiliate_disclosure_text: str = Field(
        default="Affiliate link — I may earn a commission from qualifying purchases.",
        min_length=1,
        max_length=500,
    )
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    models: ModelSelection = Field(default_factory=ModelSelection)

    @model_validator(mode="after")
    def _clip_range(self) -> Self:
        if self.clip_min_s >= self.clip_max_s:
            raise ValueError("clip_min_s must be below clip_max_s")
        return self


class PriceSnapshot(BaseModel):
    input_per_mtok: Decimal = Decimal(0)
    output_per_mtok: Decimal = Decimal(0)
    per_audio_minute: Decimal = Decimal(0)
    currency: str = "USD"


class ProviderSnapshot(BaseModel):
    provider: str
    model: str
    capability: str
    secret_ref: str
    rpm_limit: int | None = None
    rpd_limit: int | None = None
    quota_reset: QuotaReset = "utc_midnight"
    price: PriceSnapshot = Field(default_factory=PriceSnapshot)


class JobSettings(SettingsSchema):
    """What a job runs with: settings plus the resolved provider per model role."""

    providers: dict[ModelRole, ProviderSnapshot] = Field(default_factory=dict)

    @classmethod
    def of(cls, snapshot: dict[str, Any]) -> "JobSettings":
        return cls.model_validate(snapshot)

    def provider(self, role: ModelRole) -> ProviderSnapshot:
        try:
            return self.providers[role]
        except KeyError:
            raise PermanentError(f"no {role} provider in the job's settings snapshot") from None


async def load_settings(session: AsyncSession) -> SettingsSchema:
    rows = await session.execute(select(AppSetting.key, AppSetting.value))
    return SettingsSchema.model_validate({key: value for key, value in rows})
