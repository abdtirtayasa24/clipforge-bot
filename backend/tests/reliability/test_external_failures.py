"""AI provider, Google Drive, disk and YouTube failures: retried, deferred or failed visibly."""

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import NamedTuple

import httpx
import psycopg
import pytest
import respx
from pydantic import SecretStr

from clipforge import discovery_cli
from clipforge.config import Settings
from clipforge.discovery import youtube_client
from clipforge.ingest import disk
from clipforge.queue.errors import QuotaDeferred, TransientError
from tests.db import libpq_url
from tests.jobs import claim, event_types, load_job
from tests.pipeline import BOT_TOKEN, PROPOSALS, SEND_URL, Pipeline

GIB = 1024**3
NEXT_DAY = timedelta(days=1)


async def attempt(pipeline: Pipeline) -> str:
    return await pipeline.runner().run(await claim(pipeline.queue))


async def retry_attempt(pipeline: Pipeline) -> str:
    """The next attempt once its backoff has passed."""
    pipeline.clock.advance(NEXT_DAY)
    return await attempt(pipeline)


async def test_provider_timeout_is_retried_with_backoff(pipeline: Pipeline) -> None:
    job_id = await pipeline.enqueue()
    pipeline.transcription.failures = [TransientError("gemini request failed: ReadTimeout")]

    assert await attempt(pipeline) == "retry_scheduled"
    assert await pipeline.queue.claim_next("worker-a") is None, "backoff is respected"
    assert await retry_attempt(pipeline) == "completed"

    assert "retry_scheduled" in await event_types(pipeline.sessions, job_id)
    await pipeline.assert_recovered(job_id)


async def test_provider_rate_limit_defers_without_using_an_attempt(pipeline: Pipeline) -> None:
    job_id = await pipeline.enqueue()
    until = pipeline.clock() + timedelta(minutes=30)
    pipeline.transcription.failures = [QuotaDeferred("gemini is rate limited (429)", until=until)]

    assert await attempt(pipeline) == "retry_scheduled"
    job = await load_job(pipeline.sessions, job_id)
    assert (job.attempts, job.next_attempt_at) == (0, until)
    assert (await event_types(pipeline.sessions, job_id))[-1] == "quota_deferred"
    pipeline.clock.advance(timedelta(minutes=29))
    assert await pipeline.queue.claim_next("worker-a") is None

    pipeline.clock.advance(timedelta(minutes=1))
    assert await attempt(pipeline) == "completed"
    await pipeline.assert_recovered(job_id)


async def test_provider_failing_every_attempt_fails_visibly(pipeline: Pipeline) -> None:
    job_id = await pipeline.enqueue()
    pipeline.transcription.failures = [
        TransientError("gemini request failed: ReadTimeout") for _ in range(3)
    ]

    statuses = [await attempt(pipeline), await retry_attempt(pipeline)]
    statuses.append(await retry_attempt(pipeline))

    assert statuses == ["retry_scheduled", "retry_scheduled", "failed"]
    await pipeline.assert_failed_visibly(job_id, "ReadTimeout")


async def test_drive_5xx_mid_upload_resumes_the_same_session(pipeline: Pipeline) -> None:
    job_id = await pipeline.enqueue()
    pipeline.drive.chunk_server_errors = 1

    assert await attempt(pipeline) == "retry_scheduled"
    assert "HTTP 503" in str((await load_job(pipeline.sessions, job_id)).last_error)
    assert await retry_attempt(pipeline) == "completed"

    await pipeline.assert_recovered(job_id)
    assert len(pipeline.drive.sessions) == len(PROPOSALS), "the interrupted session was resumed"


async def test_ambiguous_drive_completion_adopts_the_uploaded_file(pipeline: Pipeline) -> None:
    job_id = await pipeline.enqueue()
    pipeline.drive.crash_on_complete = True

    assert await attempt(pipeline) == "retry_scheduled"
    pipeline.drive.crash_on_complete = False
    assert await retry_attempt(pipeline) == "completed"

    await pipeline.assert_recovered(job_id)
    assert len(pipeline.drive.sessions) == len(PROPOSALS), "the finished upload was adopted"


class FakeUsage(NamedTuple):
    total: int
    used: int
    free: int


async def test_full_disk_defers_downloads_then_fails_visibly(
    pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        disk.shutil, "disk_usage", lambda _path: FakeUsage(20 * GIB, 19 * GIB, 1 * GIB)
    )
    job_id = await pipeline.enqueue(
        kind="url",
        source_url="https://media.example.com/source.mp4",
        state="pending",
        local_path=None,
        sha256=None,
        media_info=None,
    )

    statuses = [await attempt(pipeline), await retry_attempt(pipeline)]
    statuses.append(await retry_attempt(pipeline))

    assert statuses == ["retry_scheduled", "retry_scheduled", "failed"]
    await pipeline.assert_failed_visibly(job_id, "not enough free disk")


@pytest.fixture
def no_sources(migrated_database_url: str) -> Iterator[None]:
    url = libpq_url(migrated_database_url)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM youtube_sources")
        conn.execute(
            "INSERT INTO youtube_sources (kind, name, region_code) "
            "VALUES ('trending', 'Trending ID', 'ID')"
        )
    yield
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM youtube_sources")


@pytest.mark.usefixtures("no_sources")
@pytest.mark.parametrize(
    "youtube_failure",
    [
        httpx.Response(403, json={"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}),
        httpx.Response(503, json={"error": {"code": 503}}),
        httpx.ConnectError("network is unreachable"),
    ],
    ids=["quota_exceeded", "unavailable", "network"],
)
async def test_discovery_api_failure_is_reported_and_exits_non_zero(
    migrated_database_url: str,
    admin_chat: int,
    monkeypatch: pytest.MonkeyPatch,
    youtube_failure: httpx.Response | Exception,
) -> None:
    monkeypatch.setattr(youtube_client, "RETRY_DELAYS_S", (0.0, 0.0))
    with respx.mock(assert_all_called=False) as router:
        youtube = router.route(host="www.googleapis.com", path__startswith="/youtube/v3/")
        if isinstance(youtube_failure, Exception):
            youtube.side_effect = youtube_failure
        else:
            youtube.return_value = youtube_failure
        telegram = router.post(SEND_URL).respond(json={"ok": True, "result": {}})
        settings = Settings(telegram_bot_token=SecretStr(BOT_TOKEN))

        exit_code = await discovery_cli._run(settings, migrated_database_url, "test-key")

    assert exit_code == discovery_cli.EXIT_FAILED
    texts = [json.loads(call.request.content) for call in telegram.calls]
    [summary] = [body["text"] for body in texts if body["chat_id"] == admin_chat]
    assert "1 failed: Trending ID" in summary
    assert "test-key" not in summary
    with psycopg.connect(libpq_url(migrated_database_url)) as conn:
        row = conn.execute("SELECT last_run_at FROM youtube_sources").fetchone()
    assert row == (None,), "a failed source keeps its previous run time"
