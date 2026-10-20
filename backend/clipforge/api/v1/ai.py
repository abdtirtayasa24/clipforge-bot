import os
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import parse_selection, snapshot_of
from clipforge.api.deps import DbSession, SessionsDep
from clipforge.auth.deps import require_admin
from clipforge.db.models import AiProviderConfig
from clipforge.settings_schema import load_settings

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require_admin)])


def _decimal_text(value: Decimal) -> str:
    return f"{value.normalize():f}"


class PricesOut(BaseModel):
    input_per_mtok: str
    output_per_mtok: str
    per_audio_minute: str
    currency: str


class QuotaOut(BaseModel):
    rpm_limit: int | None
    rpd_limit: int | None
    reset: str
    used_today: int


class ProviderOut(BaseModel):
    id: uuid.UUID
    provider: str
    model: str
    capability: str
    enabled: bool
    selected_for: list[str]
    secret_configured: bool
    prices: PricesOut
    quota: QuotaOut


class ProviderList(BaseModel):
    items: list[ProviderOut]


def _selected_roles(selection: dict[str, str | None], config: AiProviderConfig) -> list[str]:
    roles = []
    for role, value in selection.items():
        if value is None or role.removesuffix("_fallback") != config.capability:
            continue
        provider, model = parse_selection(value)
        if model == config.model and provider in (None, config.provider):
            roles.append(role)
    return roles


@router.get("/providers")
async def list_providers(session: DbSession, sessions: SessionsDep) -> ProviderList:
    """Configured models with prices and today's usage; secrets are only reported as set."""
    selection = (await load_settings(session)).models.model_dump()
    configs = await session.scalars(
        select(AiProviderConfig).order_by(
            AiProviderConfig.capability, AiProviderConfig.provider, AiProviderConfig.model
        )
    )
    guard = QuotaGuard(sessions)
    items = []
    for config in configs:
        snapshot = snapshot_of(config)
        used_today = await guard.used_today(config.provider, config.model, snapshot.quota_reset)
        items.append(
            ProviderOut(
                id=config.id,
                provider=config.provider,
                model=config.model,
                capability=config.capability,
                enabled=config.enabled,
                selected_for=_selected_roles(selection, config),
                secret_configured=bool(os.environ.get(config.secret_ref)),
                prices=PricesOut(
                    input_per_mtok=_decimal_text(config.price_input_per_mtok),
                    output_per_mtok=_decimal_text(config.price_output_per_mtok),
                    per_audio_minute=_decimal_text(config.price_per_audio_minute),
                    currency=config.currency,
                ),
                quota=QuotaOut(
                    rpm_limit=config.rpm_limit,
                    rpd_limit=config.rpd_limit,
                    reset=snapshot.quota_reset,
                    used_today=used_today,
                ),
            )
        )
    return ProviderList(items=items)
