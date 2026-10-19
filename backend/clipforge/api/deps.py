from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.config import Settings


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] | None = request.app.state.session_factory
    if sessions is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database is not configured")
    async with sessions() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
