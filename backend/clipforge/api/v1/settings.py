import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from clipforge.api.deps import DbSession
from clipforge.api.errors import Unprocessable
from clipforge.auth.deps import CurrentAdmin, require_admin
from clipforge.settings_schema import AdminSettings
from clipforge.settings_service import SettingsInvalid, current_settings, update_settings

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_admin)])

Price = Decimal | None


class DiscoveryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_code: str | None = None
    top_categories: int | None = None
    chart_pages: int | None = None
    per_category_results: int | None = None
    max_new_eligible_per_run: int | None = None
    excluded_category_ids: list[str] | None = None


class ModelsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcription: str | None = None
    analysis: str | None = None
    rerank: str | None = None
    analysis_fallback: str | None = None
    embedding: str | None = None


class ProviderPatch(BaseModel):
    """Prices and quota limits of one `ai_provider_configs` row; null limit = unlimited."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    enabled: bool | None = None
    rpm_limit: int | None = Field(default=None, ge=1)
    rpd_limit: int | None = Field(default=None, ge=1)
    price_input_per_mtok: Price = Field(default=None, ge=0, max_digits=12, decimal_places=6)
    price_output_per_mtok: Price = Field(default=None, ge=0, max_digits=12, decimal_places=6)
    price_per_audio_minute: Price = Field(default=None, ge=0, max_digits=12, decimal_places=6)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class SettingsPatch(BaseModel):
    """Only the keys sent are changed; bounds are enforced on the merged result."""

    model_config = ConfigDict(extra="forbid")

    min_score: float | None = None
    clip_min_s: float | None = None
    clip_max_s: float | None = None
    max_clips: int | None = None
    max_overlap: float | None = None
    audio_chunk_minutes: int | None = None
    reserved_disk_bytes: int | None = None
    daily_job_cap: int | None = None
    permitted_download_domains: list[str] | None = None
    title_overlay_enabled: bool | None = None
    subject_reframing_enabled: bool | None = None
    affiliate_links_enabled: bool | None = None
    affiliate_min_relevance: float | None = None
    affiliate_merchant_domains: list[str] | None = None
    affiliate_disclosure_text: str | None = None
    telegram_notifications_enabled: bool | None = None
    discovery: DiscoveryPatch | None = None
    models: ModelsPatch | None = None
    providers: list[ProviderPatch] = Field(default_factory=list, max_length=50)


@router.get("")
async def get_settings(session: DbSession) -> AdminSettings:
    return await current_settings(session)


@router.patch("")
async def patch_settings(
    body: SettingsPatch, admin: CurrentAdmin, session: DbSession
) -> AdminSettings:
    changes = body.model_dump(exclude_unset=True, exclude={"providers"})
    providers = [change.model_dump(exclude_unset=True) for change in body.providers]
    try:
        return await update_settings(session, admin.id, changes, providers)
    except SettingsInvalid as exc:
        raise Unprocessable("settings validation failed", details=exc.errors) from None
