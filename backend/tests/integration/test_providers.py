import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from clipforge.ai.adapters.fake import FakeTranscriptionProvider
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import (
    ProviderConfigError,
    ProviderRegistry,
    resolve_providers,
    snapshot_job_settings,
)
from clipforge.ai.usage import ProviderCall, usage_recorder
from clipforge.db.models import AiProviderConfig, AppSetting, ProviderUsage
from clipforge.queue.errors import QuotaDeferred
from clipforge.queue.repository import LEASE_DURATION, JobQueue
from clipforge.settings_schema import JobSettings, ModelSelection, ProviderSnapshot
from tests.api import admin_client
from tests.clock import FakeClock
from tests.db import libpq_url
from tests.jobs import Sessions, claim, event_types, load_job, make_queued_job

TRANSCRIBE = "gemini-3.5-transcribe"


@pytest.fixture
async def settings_restored(sessions: Sessions) -> AsyncIterator[None]:
    """Restores app settings and provider configs edited by a test."""
    async with sessions() as session:
        settings = dict((await session.execute(select(AppSetting.key, AppSetting.value))).all())
        configs = {
            config.id: (
                config.enabled,
                config.price_per_audio_minute,
                config.price_input_per_mtok,
                config.rpd_limit,
            )
            for config in await session.scalars(select(AiProviderConfig))
        }
    yield
    async with sessions.begin() as session:
        for key, value in settings.items():
            await session.execute(
                update(AppSetting).where(AppSetting.key == key).values(value=value)
            )
        for config_id, (enabled, audio, tokens, rpd) in configs.items():
            await session.execute(
                update(AiProviderConfig)
                .where(AiProviderConfig.id == config_id)
                .values(
                    enabled=enabled,
                    price_per_audio_minute=audio,
                    price_input_per_mtok=tokens,
                    rpd_limit=rpd,
                )
            )


async def set_setting(sessions: Sessions, key: str, value: object) -> None:
    async with sessions.begin() as session:
        await session.execute(update(AppSetting).where(AppSetting.key == key).values(value=value))


async def set_price(sessions: Sessions, model: str, per_audio_minute: str) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(AiProviderConfig)
            .where(AiProviderConfig.model == model)
            .values(price_per_audio_minute=Decimal(per_audio_minute))
        )


@pytest.fixture
def snapshot_queue(sessions: Sessions, clock: FakeClock) -> JobQueue:
    return JobQueue(
        sessions, clock=clock, jitter=lambda: 0.0, snapshot_settings=snapshot_job_settings
    )


async def test_seeded_models_resolve_for_every_role(sessions: Sessions) -> None:
    async with sessions() as session:
        providers = await resolve_providers(session, ModelSelection())

    assert {role: p.model for role, p in providers.items()} == {
        "transcription": TRANSCRIBE,
        "analysis": "gemini-3.5-flash-lite",
        "rerank": "gemini-3-flash-preview",
        "analysis_fallback": "nvidia/nemotron-3.5-super-vl:free",
        "embedding": "gemini-embedding-2",
    }
    assert providers["analysis_fallback"].provider == "openrouter"
    assert providers["transcription"].quota_reset == "pacific_midnight"


@pytest.mark.usefixtures("settings_restored")
async def test_disabled_or_unknown_model_is_a_config_error(sessions: Sessions) -> None:
    async with sessions() as session:
        with pytest.raises(ProviderConfigError, match="analysis"):
            await resolve_providers(session, ModelSelection(analysis="openrouter:unknown"))
    async with sessions.begin() as session:
        await session.execute(
            update(AiProviderConfig)
            .where(AiProviderConfig.model == TRANSCRIBE)
            .values(enabled=False)
        )

    async with sessions() as session:
        with pytest.raises(ProviderConfigError, match="transcription"):
            await resolve_providers(session, ModelSelection())


@pytest.mark.usefixtures("settings_restored")
async def test_settings_are_snapshotted_once_per_job(
    snapshot_queue: JobQueue, sessions: Sessions, clock: FakeClock
) -> None:
    await set_setting(sessions, "min_score", 75)
    first_id = await make_queued_job(snapshot_queue, sessions)
    first = await claim(snapshot_queue)

    await set_setting(sessions, "min_score", 80)
    await set_setting(sessions, "models", ModelSelection(analysis_fallback=None).model_dump())
    await set_price(sessions, TRANSCRIBE, "0.5")
    clock.advance(LEASE_DURATION + timedelta(seconds=1))
    reclaimed = await claim(snapshot_queue)
    second_id = await make_queued_job(snapshot_queue, sessions)
    second = await claim(snapshot_queue)

    old, new = JobSettings.of(reclaimed.settings_snapshot), JobSettings.of(second.settings_snapshot)
    assert reclaimed.lease.job_id == first_id and second.lease.job_id == second_id
    assert reclaimed.settings_snapshot == first.settings_snapshot
    assert (old.min_score, new.min_score) == (75, 80)
    assert "analysis_fallback" in old.providers and "analysis_fallback" not in new.providers
    assert old.provider("transcription").price.per_audio_minute == 0
    assert new.provider("transcription").price.per_audio_minute == Decimal("0.5")
    assert (await load_job(sessions, first_id)).settings_snapshot == first.settings_snapshot


async def test_queue_without_a_snapshot_function_keeps_the_stored_snapshot(
    queue: JobQueue, sessions: Sessions
) -> None:
    await make_queued_job(queue, sessions)

    assert (await claim(queue)).settings_snapshot == {}


@pytest.mark.usefixtures("settings_restored")
async def test_usage_cost_uses_the_job_price_snapshot(
    snapshot_queue: JobQueue, sessions: Sessions
) -> None:
    await set_price(sessions, TRANSCRIBE, "0.006")
    job_id = await make_queued_job(snapshot_queue, sessions)
    provider = JobSettings.of((await claim(snapshot_queue)).settings_snapshot).provider(
        "transcription"
    )
    record = usage_recorder(sessions, provider=provider, job_id=job_id, stage="transcribed")
    await record(ProviderCall(success=False, latency_ms=900, error_code="503"))
    await record(ProviderCall(success=True, latency_ms=1200, audio_seconds=600))

    await set_price(sessions, TRANSCRIBE, "1")
    await record(ProviderCall(success=True, latency_ms=1100, audio_seconds=60))

    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(ProviderUsage)
                .where(ProviderUsage.job_id == job_id)
                .order_by(ProviderUsage.created_at, ProviderUsage.latency_ms)
            )
        )
    assert [(r.success, r.estimated_cost) for r in rows] == [
        (False, Decimal("0")),
        (True, Decimal("0.06")),
        (True, Decimal("0.006")),
    ]
    assert {(r.provider, r.model, r.capability, r.stage) for r in rows} == {
        ("gemini", TRANSCRIBE, "transcription", "transcribed")
    }
    assert rows[1].audio_seconds == Decimal("600.000")


async def test_registry_builds_adapters_with_their_secret(sessions: Sessions) -> None:
    async with sessions() as session:
        provider = (await resolve_providers(session, ModelSelection()))["transcription"]
    seen: list[tuple[ProviderSnapshot, str]] = []

    def factory(snapshot: ProviderSnapshot, key: str) -> FakeTranscriptionProvider:
        seen.append((snapshot, key))
        return FakeTranscriptionProvider()

    registry = ProviderRegistry(transcription={"gemini": factory}, environ={"GEMINI_API_KEY": "k"})

    assert isinstance(registry.transcription(provider), FakeTranscriptionProvider)
    assert seen == [(provider, "k")]
    with pytest.raises(ProviderConfigError, match="GEMINI_API_KEY"):
        ProviderRegistry(transcription={"gemini": factory}, environ={}).transcription(provider)
    with pytest.raises(ProviderConfigError, match="adapter"):
        ProviderRegistry(environ={"GEMINI_API_KEY": "k"}).transcription(provider)


def pacific_provider(rpd_limit: int) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider="gemini",
        model=f"quota-test-{uuid.uuid4().hex[:8]}",
        capability="analysis",
        secret_ref="GEMINI_API_KEY",
        rpd_limit=rpd_limit,
        quota_reset="pacific_midnight",
    )


async def add_usage(sessions: Sessions, provider: ProviderSnapshot, *at: datetime) -> None:
    async with sessions.begin() as session:
        session.add_all(
            ProviderUsage(
                provider=provider.provider,
                model=provider.model,
                capability=provider.capability,
                success=True,
                latency_ms=1,
                created_at=when,
            )
            for when in at
        )


async def test_daily_budget_counts_only_the_current_pacific_day(sessions: Sessions) -> None:
    # 2026-03-08 is the spring-forward day: it started at 08:00 UTC and ends at 07:00 UTC.
    now = datetime(2026, 3, 8, 20, tzinfo=UTC)
    provider = pacific_provider(rpd_limit=3)
    await add_usage(
        sessions,
        provider,
        datetime(2026, 3, 8, 7, 59, tzinfo=UTC),
        datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
        datetime(2026, 3, 8, 19, tzinfo=UTC),
    )
    guard = QuotaGuard(sessions, clock=lambda: now)

    await guard.reserve(provider, 1)
    with pytest.raises(QuotaDeferred) as deferred:
        await guard.reserve(provider, 2)

    assert deferred.value.until == datetime(2026, 3, 9, 7, tzinfo=UTC)
    assert await guard.used_today(provider.provider, provider.model, "pacific_midnight") == 2


async def test_quota_deferral_reschedules_without_using_an_attempt(
    queue: JobQueue, sessions: Sessions, clock: FakeClock
) -> None:
    job_id = await make_queued_job(queue, sessions)
    lease = (await claim(queue)).lease
    until = clock.now + timedelta(hours=9)

    status = await queue.handle_error(lease, QuotaDeferred("daily quota used", until=until))

    job = await load_job(sessions, job_id)
    assert status == "retry_scheduled"
    assert (job.status, job.attempts, job.next_attempt_at) == ("retry_scheduled", 0, until)
    assert job.lease_token is None
    assert (await event_types(sessions, job_id))[-1] == "quota_deferred"
    assert await queue.claim_next("w") is None
    clock.advance(timedelta(hours=9))
    assert (await claim(queue)).attempt == 1


@pytest.fixture
def client(migrated_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-gemini-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with admin_client(migrated_database_url) as test_client:
        yield test_client


async def test_providers_api_lists_configs_without_secrets(
    client: TestClient, sessions: Sessions, migrated_database_url: str
) -> None:
    async with sessions.begin() as session:
        await session.execute(delete(ProviderUsage).where(ProviderUsage.model == TRANSCRIBE))
    await add_usage(
        sessions,
        ProviderSnapshot(
            provider="gemini", model=TRANSCRIBE, capability="transcription", secret_ref="X"
        ),
        datetime.now(UTC),
        datetime.now(UTC),
    )

    response = client.get("/api/v1/ai/providers")

    assert response.status_code == 200
    assert "super-secret-gemini-key" not in response.text
    assert "secret_ref" not in response.text
    items = {item["model"]: item for item in response.json()["items"]}
    transcribe = items[TRANSCRIBE]
    assert transcribe["selected_for"] == ["transcription"]
    assert transcribe["secret_configured"] is True
    assert transcribe["quota"] == {
        "rpm_limit": 5,
        "rpd_limit": 100,
        "reset": "pacific_midnight",
        "used_today": 2,
    }
    assert transcribe["prices"] == {
        "input_per_mtok": "0",
        "output_per_mtok": "0",
        "per_audio_minute": "0",
        "currency": "USD",
    }
    fallback = items["nvidia/nemotron-3.5-super-vl:free"]
    assert (fallback["selected_for"], fallback["secret_configured"]) == (
        ["analysis_fallback"],
        False,
    )


def test_providers_api_requires_an_admin(migrated_database_url: str) -> None:
    with admin_client(migrated_database_url) as client:
        del client.headers["Authorization"]
        assert client.get("/api/v1/ai/providers").status_code == 401


def test_seeded_settings_validate_against_the_schema(migrated_database_url: str) -> None:
    with psycopg.connect(libpq_url(migrated_database_url)) as conn:
        rows = dict(conn.execute("SELECT key, value FROM app_settings").fetchall())

    settings = JobSettings.model_validate(rows)

    assert settings.models.analysis_fallback == "openrouter:nvidia/nemotron-3.5-super-vl:free"
