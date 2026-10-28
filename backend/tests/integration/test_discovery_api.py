import time
from collections.abc import Iterator
from datetime import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from clipforge import discovery_cli
from clipforge.config import Settings
from clipforge.discovery.service import DISCOVERY_LOCK_KEY
from tests.api import admin_client
from tests.db import libpq_url
from tests.youtube import MONITORED_CHANNEL, FakeYouTube, vid

RUN = "/api/v1/discovery/run"


@pytest.fixture
def client(migrated_database_url: str, clean_discovery: None) -> Iterator[TestClient]:
    with admin_client(migrated_database_url, youtube_api_key="test-key") as test_client:
        yield test_client


@pytest.fixture
def discovery_lock(migrated_database_url: str) -> Iterator[None]:
    """Holds the discovery lock the way a concurrent run (e.g. the timer's) would."""
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (DISCOVERY_LOCK_KEY,))
        yield
        conn.execute("SELECT pg_advisory_unlock(%s)", (DISCOVERY_LOCK_KEY,))


def execute(url: str, sql: str, *params: object) -> None:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        conn.execute(sql, params)


def add_trending(url: str) -> None:
    execute(
        url, "INSERT INTO youtube_sources (kind, name, region_code) VALUES ('trending', 'T', 'ID')"
    )


def add_channel(url: str, channel_id: str) -> None:
    execute(
        url,
        "INSERT INTO youtube_sources (kind, name, channel_id) VALUES ('channel', 'C', %s)",
        channel_id,
    )


def video_exists(url: str, youtube_video_id: str) -> bool:
    with psycopg.connect(libpq_url(url)) as conn:
        row = conn.execute(
            "SELECT 1 FROM videos WHERE youtube_video_id = %s", (youtube_video_id,)
        ).fetchone()
    return row is not None


def wait_for_sources_run(url: str) -> None:
    deadline = time.monotonic() + 5
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT bool_and(last_run_at IS NOT NULL) FROM youtube_sources"
            ).fetchone()
            if row and row[0]:
                return
            time.sleep(0.05)
    raise AssertionError("discovery did not finish within 5 s")


def test_manual_run_starts_in_background(
    client: TestClient, migrated_database_url: str, youtube: FakeYouTube
) -> None:
    add_trending(migrated_database_url)

    response = client.post(RUN)

    assert response.status_code == 202
    assert response.json() == {"status": "started"}
    wait_for_sources_run(migrated_database_url)
    assert video_exists(migrated_database_url, vid("m02"))


@pytest.mark.usefixtures("discovery_lock")
def test_manual_run_while_another_run_holds_the_lock_is_refused(
    client: TestClient, migrated_database_url: str, youtube: FakeYouTube
) -> None:
    add_trending(migrated_database_url)

    response = client.post(RUN)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "discovery is already running"
    assert youtube.requests == []


@pytest.mark.usefixtures("clean_discovery")
def test_manual_run_needs_a_youtube_api_key(migrated_database_url: str) -> None:
    with admin_client(migrated_database_url) as client:
        response = client.post(RUN)

    assert response.status_code == 503


@pytest.fixture
def cli_env(
    migrated_database_url: str, clean_discovery: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIPFORGE_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")


@pytest.mark.usefixtures("cli_env")
def test_cli_run_succeeds(migrated_database_url: str, youtube: FakeYouTube) -> None:
    add_trending(migrated_database_url)
    add_channel(migrated_database_url, MONITORED_CHANNEL)

    assert discovery_cli.main([]) == 0
    assert video_exists(migrated_database_url, vid("ch1"))


@pytest.mark.usefixtures("cli_env")
def test_cli_exits_non_zero_when_a_source_fails(
    migrated_database_url: str, youtube: FakeYouTube
) -> None:
    add_channel(migrated_database_url, "UCunknown00000000000000x")

    assert discovery_cli.main([]) == 1


@pytest.mark.usefixtures("cli_env", "discovery_lock")
def test_cli_skips_while_another_run_holds_the_lock(
    migrated_database_url: str, youtube: FakeYouTube
) -> None:
    add_trending(migrated_database_url)

    assert discovery_cli.main([]) == 0
    assert youtube.requests == []


def set_auto_discovery(url: str, *, enabled: bool, hour: int) -> None:
    execute(
        url,
        "UPDATE app_settings SET value = value || %s WHERE key = 'discovery'",
        Jsonb({"auto_enabled": enabled, "auto_hour": hour}),
    )


async def run_scheduled(url: str, at: datetime) -> int:
    return await discovery_cli._run(Settings(), url, "test-key", scheduled_at=at)


@pytest.mark.usefixtures("clean_discovery")
async def test_scheduled_run_at_the_configured_hour_runs_discovery(
    migrated_database_url: str, youtube: FakeYouTube
) -> None:
    add_trending(migrated_database_url)
    set_auto_discovery(migrated_database_url, enabled=True, hour=5)

    exit_code = await run_scheduled(migrated_database_url, datetime(2026, 10, 28, 5, 0))

    assert exit_code == discovery_cli.EXIT_OK
    assert video_exists(migrated_database_url, vid("m02"))


@pytest.mark.usefixtures("clean_discovery")
@pytest.mark.parametrize(("enabled", "hour"), [(True, 4), (False, 5)])
async def test_scheduled_run_outside_the_hour_or_when_disabled_is_skipped(
    migrated_database_url: str, youtube: FakeYouTube, enabled: bool, hour: int
) -> None:
    add_trending(migrated_database_url)
    set_auto_discovery(migrated_database_url, enabled=enabled, hour=hour)

    exit_code = await run_scheduled(migrated_database_url, datetime(2026, 10, 28, 5, 0))

    assert exit_code == discovery_cli.EXIT_OK
    assert youtube.requests == []


@pytest.mark.usefixtures("cli_env")
def test_cli_scheduled_flag_respects_disabled_auto_discovery(
    migrated_database_url: str, youtube: FakeYouTube
) -> None:
    add_trending(migrated_database_url)
    set_auto_discovery(migrated_database_url, enabled=False, hour=3)

    assert discovery_cli.main(["--scheduled"]) == 0
    assert youtube.requests == []


@pytest.mark.usefixtures("cli_env")
def test_cli_without_scheduled_flag_runs_even_when_auto_discovery_is_disabled(
    migrated_database_url: str, youtube: FakeYouTube
) -> None:
    add_trending(migrated_database_url)
    set_auto_discovery(migrated_database_url, enabled=False, hour=3)

    assert discovery_cli.main([]) == 0
    assert video_exists(migrated_database_url, vid("m02"))


@pytest.mark.usefixtures("cli_env")
@pytest.mark.parametrize("missing", ["CLIPFORGE_DATABASE_URL", "YOUTUBE_API_KEY"])
def test_cli_without_configuration_exits_with_config_error(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(missing)

    assert discovery_cli.main([]) == 2
