from collections.abc import Iterator

import psycopg
import pytest
import respx
from psycopg.types.json import Jsonb

from clipforge.discovery.youtube_client import BASE_URL
from clipforge.queue.repository import JobQueue
from tests.clock import FakeClock
from tests.db import libpq_url
from tests.jobs import Sessions
from tests.youtube import FakeYouTube, fixture_video_ids


@pytest.fixture
def queue(sessions: Sessions, clock: FakeClock) -> JobQueue:
    return JobQueue(sessions, clock=clock, jitter=lambda: 0.0)


@pytest.fixture
def youtube() -> Iterator[FakeYouTube]:
    fake = FakeYouTube()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.route().mock(side_effect=fake.handle)
        yield fake


@pytest.fixture
def clean_discovery(migrated_database_url: str) -> Iterator[None]:
    """No sources and none of the fixture videos; discovery settings restored afterwards."""

    def reset(conn: psycopg.Connection) -> None:
        conn.execute("DELETE FROM youtube_sources")
        conn.execute(
            "DELETE FROM videos WHERE youtube_video_id = ANY(%s)", (sorted(fixture_video_ids()),)
        )

    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        reset(conn)
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'discovery'").fetchone()
        assert row is not None
        yield
        reset(conn)
        conn.execute("UPDATE app_settings SET value = %s WHERE key = 'discovery'", (Jsonb(row[0]),))
