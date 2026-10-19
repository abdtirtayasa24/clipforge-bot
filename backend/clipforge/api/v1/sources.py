import uuid
from datetime import datetime
from typing import Literal, Self

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from clipforge.api.deps import DbSession
from clipforge.api.errors import Conflict, NotFound
from clipforge.auth.deps import require_admin
from clipforge.db.models import YoutubeSource

router = APIRouter(prefix="/sources", tags=["sources"], dependencies=[Depends(require_admin)])

CHANNEL_ID_PATTERN = r"^UC[A-Za-z0-9_-]{22}$"
REGION_CODE_PATTERN = r"^[A-Z]{2}$"


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["trending", "channel"]
    name: str = Field(min_length=1, max_length=200)
    channel_id: str | None = Field(default=None, pattern=CHANNEL_ID_PATTERN)
    region_code: str | None = Field(default=None, pattern=REGION_CODE_PATTERN)

    @model_validator(mode="after")
    def _channel_id_matches_kind(self) -> Self:
        if (self.kind == "channel") != (self.channel_id is not None):
            raise ValueError("channel_id is required for channel sources and only for them")
        return self


class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    region_code: str | None = Field(default=None, pattern=REGION_CODE_PATTERN)
    enabled: bool | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        for field in ("name", "enabled"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    name: str
    channel_id: str | None
    region_code: str | None
    enabled: bool
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SourceList(BaseModel):
    items: list[SourceOut]


async def _get_source(session: DbSession, source_id: uuid.UUID) -> YoutubeSource:
    source = await session.get(YoutubeSource, source_id)
    if source is None:
        raise NotFound("source not found")
    return source


@router.get("")
async def list_sources(session: DbSession) -> SourceList:
    sources = await session.scalars(select(YoutubeSource).order_by(YoutubeSource.created_at))
    return SourceList(items=[SourceOut.model_validate(source) for source in sources])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_source(body: SourceCreate, session: DbSession) -> SourceOut:
    source = YoutubeSource(**body.model_dump())
    session.add(source)
    try:
        await session.commit()
    except IntegrityError:
        raise Conflict("a source for this channel already exists") from None
    await session.refresh(source)
    return SourceOut.model_validate(source)


@router.patch("/{source_id}")
async def update_source(source_id: uuid.UUID, body: SourceUpdate, session: DbSession) -> SourceOut:
    source = await _get_source(session, source_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    await session.commit()
    await session.refresh(source)
    return SourceOut.model_validate(source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_source(source_id: uuid.UUID, session: DbSession) -> Response:
    source = await _get_source(session, source_id)
    source.enabled = False
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
