import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    video_id: uuid.UUID
    media_asset_id: uuid.UUID | None
    status: str
    stage: str
    attempts: int
    max_attempts: int
    cancel_requested: bool
    last_error: str | None
    next_attempt_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class JobEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    stage: str | None
    status: str | None
    attempt: int | None
    message: str | None
    details: dict[str, Any]
    created_at: datetime


class CostOut(BaseModel):
    currency: str
    estimated: str


def decimal_text(value: Decimal) -> str:
    return f"{value.normalize():f}"


def cost_lines(costs: Mapping[str, Decimal]) -> list[CostOut]:
    return [
        CostOut(currency=currency, estimated=decimal_text(total))
        for currency, total in costs.items()
    ]


class JobDetail(JobOut):
    events: list[JobEventOut]
    costs: list[CostOut]


class JobPage(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int
