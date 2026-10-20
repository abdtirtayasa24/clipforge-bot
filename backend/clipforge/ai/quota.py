"""Client-side quota guard: RPM token bucket plus a daily request budget (ADR-028)."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.models import ProviderUsage
from clipforge.queue.errors import PermanentError, QuotaDeferred
from clipforge.queue.repository import Clock, utc_now
from clipforge.settings_schema import ProviderSnapshot, QuotaReset

RESET_ZONES: Mapping[QuotaReset, ZoneInfo] = {
    "pacific_midnight": ZoneInfo("America/Los_Angeles"),
    "utc_midnight": ZoneInfo("UTC"),
}

# A 429 without Retry-After is usually the per-minute limit; the daily budget is enforced by
# `reserve` before the stage starts.
DEFAULT_RATE_LIMIT_WAIT = timedelta(minutes=5)

Sleep = Callable[[float], Awaitable[None]]


def day_start(now: datetime, reset: QuotaReset) -> datetime:
    """Start of the provider's current quota day, in UTC."""
    zone = RESET_ZONES[reset]
    midnight = datetime.combine(now.astimezone(zone).date(), time(0), tzinfo=zone)
    return midnight.astimezone(UTC)


def next_reset(now: datetime, reset: QuotaReset) -> datetime:
    zone = RESET_ZONES[reset]
    tomorrow = now.astimezone(zone).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0), tzinfo=zone).astimezone(UTC)


def retry_after_seconds(headers: Mapping[str, str], now: datetime) -> float | None:
    """`Retry-After` as delay-seconds or an HTTP date; None when absent or unparsable."""
    value = headers.get("retry-after")
    if not value:
        return None
    if value.strip().isdigit():
        return float(value.strip())
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


class _Bucket:
    def __init__(self, per_minute: int, now: datetime) -> None:
        self.capacity = float(per_minute)
        self.tokens = float(per_minute)
        self.rate_per_s = per_minute / 60
        self.updated = now

    def refill(self, now: datetime) -> None:
        elapsed = max(0.0, (now - self.updated).total_seconds())
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_s)
        self.updated = now


class QuotaGuard:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        clock: Clock = utc_now,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._sleep = sleep
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def now(self) -> datetime:
        return self._clock()

    async def used_today(self, provider: str, model: str, reset: QuotaReset) -> int:
        since = day_start(self._clock(), reset)
        async with self._sessions() as session:
            count = await session.scalar(
                select(func.count()).where(
                    ProviderUsage.provider == provider,
                    ProviderUsage.model == model,
                    ProviderUsage.created_at >= since,
                )
            )
        return count or 0

    async def reserve(self, provider: ProviderSnapshot, calls: int) -> None:
        """Raises QuotaDeferred until the next reset when today's budget can't fit `calls`."""
        limit = provider.rpd_limit
        if limit is None:
            return
        if calls > limit:
            raise PermanentError(
                f"{provider.model} needs {calls} requests but its daily limit is {limit}"
            )
        used = await self.used_today(provider.provider, provider.model, provider.quota_reset)
        if used + calls > limit:
            until = next_reset(self._clock(), provider.quota_reset)
            raise QuotaDeferred(
                f"{provider.model} daily quota used ({used}/{limit}); {calls} more needed",
                until=until,
            )

    async def acquire(self, provider: ProviderSnapshot) -> None:
        """Waits for a request slot under the provider's per-minute limit."""
        if provider.rpm_limit is None:
            return
        key = (provider.provider, provider.model)
        bucket = self._buckets.get(key)
        if bucket is None or bucket.capacity != provider.rpm_limit:
            bucket = self._buckets[key] = _Bucket(provider.rpm_limit, self._clock())
        bucket.refill(self._clock())
        while bucket.tokens < 1:
            await self._sleep((1 - bucket.tokens) / bucket.rate_per_s)
            bucket.refill(self._clock())
        bucket.tokens -= 1

    def deferral(self, provider: ProviderSnapshot, retry_after_s: float | None) -> QuotaDeferred:
        """For a 429 the adapter won't wait out itself: the job resumes after Retry-After."""
        wait = (
            timedelta(seconds=retry_after_s)
            if retry_after_s is not None
            else DEFAULT_RATE_LIMIT_WAIT
        )
        until = self._clock() + wait
        return QuotaDeferred(f"{provider.model} is rate limited", until=until)
