from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
import respx
from psycopg.types.json import Jsonb
from sqlalchemy import update

from clipforge.db.models import ProcessingJob
from clipforge.db.session import create_engine, create_session_factory
from clipforge.discovery.youtube_client import BASE_URL
from clipforge.queue.repository import ACTIVE_STATUSES, JobQueue
from tests.clock import FakeClock
from tests.db import libpq_url
from tests.fixtures.make_media import make_media
from tests.jobs import Sessions
from tests.youtube import FakeYouTube, fixture_video_ids


@pytest.fixture
async def sessions(migrated_database_url: str) -> AsyncIterator[Sessions]:
    """Sessions on the shared test database with no claimable job left by earlier tests."""
    engine = create_engine(migrated_database_url, pool_size=3)
    factory = create_session_factory(engine)
    async with factory.begin() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.status.in_(ACTIVE_STATUSES))
            .values(status="cancelled", lease_token=None, lease_expires_at=None)
        )
    yield factory
    await engine.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


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


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def valid_source(media_dir: Path, require_ffmpeg: None) -> Path:
    """A 31-minute source that passes validation (smallest valid duration plus a margin)."""
    return make_media(media_dir / "valid.mp4", duration_s=31 * 60)
