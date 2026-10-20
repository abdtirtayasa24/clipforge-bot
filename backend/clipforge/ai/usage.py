"""Per-call provider usage with estimated cost from the job's price snapshot (ADR-024)."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.models import ProviderUsage
from clipforge.settings_schema import PriceSnapshot, ProviderSnapshot

COST_QUANTUM = Decimal("0.00000001")
_PER_MILLION = Decimal(1_000_000)
_SECONDS_PER_MINUTE = Decimal(60)


@dataclass(frozen=True)
class ProviderCall:
    """One request to a provider, successful or not; retries are separate calls."""

    success: bool
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    audio_seconds: float | None = None
    error_code: str | None = None


UsageRecorder = Callable[[ProviderCall], Awaitable[None]]


def estimate_cost(call: ProviderCall, price: PriceSnapshot) -> Decimal:
    cost = (
        Decimal(call.input_tokens or 0) * price.input_per_mtok / _PER_MILLION
        + Decimal(call.output_tokens or 0) * price.output_per_mtok / _PER_MILLION
        + Decimal(str(call.audio_seconds or 0)) * price.per_audio_minute / _SECONDS_PER_MINUTE
    )
    return cost.quantize(COST_QUANTUM)


async def record_usage(
    sessions: async_sessionmaker[AsyncSession],
    call: ProviderCall,
    *,
    provider: ProviderSnapshot,
    job_id: uuid.UUID | None,
    stage: str | None,
) -> None:
    async with sessions.begin() as session:
        session.add(
            ProviderUsage(
                job_id=job_id,
                stage=stage,
                provider=provider.provider,
                model=provider.model,
                capability=provider.capability,
                success=call.success,
                error_code=call.error_code,
                latency_ms=call.latency_ms,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                audio_seconds=(
                    Decimal(str(call.audio_seconds)) if call.audio_seconds is not None else None
                ),
                estimated_cost=estimate_cost(call, provider.price),
                currency=provider.price.currency,
            )
        )


def usage_recorder(
    sessions: async_sessionmaker[AsyncSession],
    *,
    provider: ProviderSnapshot,
    job_id: uuid.UUID | None,
    stage: str | None,
) -> UsageRecorder:
    async def record(call: ProviderCall) -> None:
        await record_usage(sessions, call, provider=provider, job_id=job_id, stage=stage)

    return record
