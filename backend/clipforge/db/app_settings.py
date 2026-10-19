from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clipforge.db.models import AppSetting


async def get_setting(session: AsyncSession, key: str) -> Any:
    """The stored JSON value of an app setting, or None when the key is absent."""
    return await session.scalar(select(AppSetting.value).where(AppSetting.key == key))


async def permitted_download_domains(session: AsyncSession) -> list[str]:
    value = await get_setting(session, "permitted_download_domains")
    return [entry for entry in value if isinstance(entry, str)] if isinstance(value, list) else []
