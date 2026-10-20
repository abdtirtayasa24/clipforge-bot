from datetime import UTC, datetime, timedelta

import pytest

from clipforge.ai.quota import QuotaGuard, day_start, next_reset, retry_after_seconds
from clipforge.queue.errors import PermanentError
from clipforge.settings_schema import ProviderSnapshot, QuotaReset
from tests.clock import FakeClock


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "reset", "start", "following"),
    [
        # Pacific standard time (UTC-8).
        (utc(2026, 1, 15, 12), "pacific_midnight", utc(2026, 1, 15, 8), utc(2026, 1, 16, 8)),
        # Still the previous Pacific day before 08:00 UTC.
        (utc(2026, 1, 15, 7, 59), "pacific_midnight", utc(2026, 1, 14, 8), utc(2026, 1, 15, 8)),
        # Spring forward (2026-03-08): the day starts in PST and ends in PDT.
        (utc(2026, 3, 8, 10), "pacific_midnight", utc(2026, 3, 8, 8), utc(2026, 3, 9, 7)),
        # Fall back (2026-11-01): the day starts in PDT and ends in PST.
        (utc(2026, 11, 1, 12), "pacific_midnight", utc(2026, 11, 1, 7), utc(2026, 11, 2, 8)),
        (utc(2026, 7, 4, 23, 30), "utc_midnight", utc(2026, 7, 4), utc(2026, 7, 5)),
    ],
)
def test_quota_day_boundaries(
    now: datetime, reset: QuotaReset, start: datetime, following: datetime
) -> None:
    assert day_start(now, reset) == start
    assert next_reset(now, reset) == following


NOW = utc(2026, 10, 20, 12)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"retry-after": "30"}, 30.0),
        ({"retry-after": " 7 "}, 7.0),
        ({"retry-after": "Tue, 20 Oct 2026 12:01:00 GMT"}, 60.0),
        ({"retry-after": "Tue, 20 Oct 2026 11:00:00 GMT"}, 0.0),
        ({"retry-after": "soon"}, None),
        ({}, None),
    ],
)
def test_retry_after_header(headers: dict[str, str], expected: float | None) -> None:
    assert retry_after_seconds(headers, NOW) == expected


def provider(**overrides: object) -> ProviderSnapshot:
    values: dict[str, object] = {
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "capability": "analysis",
        "secret_ref": "GEMINI_API_KEY",
    }
    return ProviderSnapshot.model_validate(values | overrides)


class SleepRecorder:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock.advance(timedelta(seconds=seconds))


async def unused_sessions() -> None:
    raise AssertionError("no database access expected")


def guard(clock: FakeClock, sleep: SleepRecorder) -> QuotaGuard:
    return QuotaGuard(unused_sessions, clock=clock, sleep=sleep)  # type: ignore[arg-type]


async def test_rpm_bucket_waits_once_the_burst_is_used() -> None:
    clock = FakeClock(NOW)
    sleep = SleepRecorder(clock)
    quota = guard(clock, sleep)
    limited = provider(rpm_limit=2)

    for _ in range(3):
        await quota.acquire(limited)

    assert sleep.waits == [pytest.approx(30.0)]


async def test_rpm_bucket_refills_over_time() -> None:
    clock = FakeClock(NOW)
    sleep = SleepRecorder(clock)
    quota = guard(clock, sleep)
    limited = provider(rpm_limit=2)
    await quota.acquire(limited)
    await quota.acquire(limited)

    clock.advance(timedelta(minutes=1))
    await quota.acquire(limited)
    await quota.acquire(limited)

    assert sleep.waits == []


async def test_unlimited_provider_never_waits() -> None:
    clock = FakeClock(NOW)
    sleep = SleepRecorder(clock)

    for _ in range(100):
        await guard(clock, sleep).acquire(provider())

    assert sleep.waits == []


async def test_reserving_more_than_a_whole_day_is_permanent() -> None:
    clock = FakeClock(NOW)

    with pytest.raises(PermanentError, match="daily limit"):
        await guard(clock, SleepRecorder(clock)).reserve(provider(rpd_limit=5), 6)


@pytest.mark.parametrize(("retry_after", "wait"), [(42.0, 42.0), (None, 300.0)])
def test_rate_limit_deferral(retry_after: float | None, wait: float) -> None:
    clock = FakeClock(NOW)

    deferred = guard(clock, SleepRecorder(clock)).deferral(provider(), retry_after)

    assert deferred.until == NOW + timedelta(seconds=wait)
