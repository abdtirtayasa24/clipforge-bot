"""Typed tunable settings and the per-job snapshot taken at claim (ADR-026, ADR-024)."""

import re
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clipforge.db.models import AppSetting
from clipforge.discovery.service import DiscoverySettings
from clipforge.queue.errors import PermanentError
from clipforge.security.url_guard import is_youtube_host

QuotaReset = Literal["pacific_midnight", "utc_midnight"]
ModelRole = Literal["transcription", "analysis", "rerank", "analysis_fallback", "embedding"]

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def hostname_entry(entry: str) -> str:
    """A lowercase dotted hostname or `*.suffix`; IPs, schemes, ports and paths are refused."""
    entry = entry.strip().lower().rstrip(".")
    labels = entry.removeprefix("*.").split(".")
    if len(labels) < 2 or labels[-1].isdigit() or not all(map(_HOST_LABEL.match, labels)):
        raise ValueError("entries must be a hostname or *.suffix, without scheme, port or path")
    return entry


def download_domain(entry: str) -> str:
    """A permitted download domain; YouTube and Google video hosts are refused (ADR-018)."""
    entry = hostname_entry(entry)
    if is_youtube_host(entry.removeprefix("*.")):
        raise ValueError("YouTube and Google video hosts cannot be permitted download domains")
    return entry


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
    # Tracking or redirect hosts an affiliate URL may use besides its destination domain.
    affiliate_merchant_domains: list[str] = Field(default_factory=list, max_length=100)
    affiliate_disclosure_text: str = Field(
        default="Affiliate link — I may earn a commission from qualifying purchases.",
        min_length=1,
        max_length=500,
    )
    telegram_notifications_enabled: bool = True
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    models: ModelSelection = Field(default_factory=ModelSelection)

    @field_validator("permitted_download_domains")
    @classmethod
    def _download_domains(cls, entries: list[str]) -> list[str]:
        return [download_domain(entry) for entry in entries]

    @field_validator("affiliate_merchant_domains")
    @classmethod
    def _merchant_domains(cls, entries: list[str]) -> list[str]:
        return [hostname_entry(entry) for entry in entries]

    @model_validator(mode="after")
    def _clip_range(self) -> Self:
        if self.clip_min_s >= self.clip_max_s:
            raise ValueError("clip_min_s must be below clip_max_s")
        return self


class AdminSettings(SettingsSchema):
    """What an administrator may set: the spec's bounds, tighter than stored jobs tolerate."""

    clip_min_s: float = Field(default=20, ge=20, le=60)
    clip_max_s: float = Field(default=60, ge=20, le=60)
    max_overlap: float = Field(default=0.2, ge=0, le=0.2)


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
