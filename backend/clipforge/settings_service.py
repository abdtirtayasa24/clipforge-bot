"""Validated, audited settings changes (ADR-026); they apply to jobs claimed afterwards."""

import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from clipforge.ai.registry import ProviderConfigError, resolve_providers
from clipforge.db.models import AiProviderConfig, AppSetting, AppSettingEvent
from clipforge.settings_schema import AdminSettings, load_settings

NON_NULL_PROVIDER_FIELDS = frozenset(
    {
        "enabled",
        "price_input_per_mtok",
        "price_output_per_mtok",
        "price_per_audio_minute",
        "currency",
    }
)


class SettingsInvalid(Exception):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("settings validation failed")
        self.errors = errors


def _error(loc: Sequence[str | int], msg: str) -> dict[str, Any]:
    return {"loc": ["body", *loc], "msg": msg, "type": "value_error"}


def _json(value: Any) -> Any:
    return f"{value.normalize():f}" if isinstance(value, Decimal) else value


async def current_settings(session: AsyncSession) -> AdminSettings:
    return AdminSettings.model_validate((await load_settings(session)).model_dump())


def _merge(current: dict[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


async def _apply_provider_changes(
    session: AsyncSession, admin_id: uuid.UUID, changes: Sequence[Mapping[str, Any]]
) -> None:
    ids = [change["id"] for change in changes]
    configs = {
        config.id: config
        for config in await session.scalars(
            select(AiProviderConfig).where(AiProviderConfig.id.in_(ids)).with_for_update()
        )
    }
    for index, change in enumerate(changes):
        config = configs.get(change["id"])
        if config is None:
            raise SettingsInvalid([_error(["providers", index, "id"], "unknown provider config")])
        old: dict[str, Any] = {}
        new: dict[str, Any] = {}
        for field, value in change.items():
            if field == "id":
                continue
            if value is None and field in NON_NULL_PROVIDER_FIELDS:
                raise SettingsInvalid([_error(["providers", index, field], "cannot be null")])
            if getattr(config, field) != value:
                old[field], new[field] = _json(getattr(config, field)), _json(value)
                setattr(config, field, value)
        if new:
            session.add(
                AppSettingEvent(
                    setting_key=f"ai_provider_configs/{config.id}",
                    old_value=old,
                    new_value=new,
                    admin_user_id=admin_id,
                )
            )
    await session.flush()


async def update_settings(
    session: AsyncSession,
    admin_id: uuid.UUID,
    changes: Mapping[str, Any],
    provider_changes: Sequence[Mapping[str, Any]] = (),
) -> AdminSettings:
    """Applies a partial update; nothing is committed when any value is invalid."""
    current = (await load_settings(session)).model_dump(mode="json")
    try:
        updated = AdminSettings.model_validate(_merge(current, changes))
    except ValidationError as exc:
        raise SettingsInvalid(
            [_error([str(part) for part in e["loc"]], e["msg"]) for e in exc.errors()]
        ) from None
    if provider_changes:
        await _apply_provider_changes(session, admin_id, provider_changes)
    try:
        await resolve_providers(session, updated.models)
    except ProviderConfigError as exc:
        raise SettingsInvalid([_error(["models"], str(exc))]) from None

    new = updated.model_dump(mode="json")
    for key in changes:
        if new[key] == current[key]:
            continue
        await session.execute(
            insert(AppSetting)
            .values(key=key, value=new[key], updated_by=admin_id)
            .on_conflict_do_update(
                index_elements=[AppSetting.key],
                set_={"value": new[key], "updated_by": admin_id},
            )
        )
        session.add(
            AppSettingEvent(
                setting_key=key, old_value=current[key], new_value=new[key], admin_user_id=admin_id
            )
        )
    await session.commit()
    return updated
