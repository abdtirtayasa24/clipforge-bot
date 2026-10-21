import asyncio
import contextlib
import os
import shutil
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from sqlalchemy import update

from clipforge.db.models import ProcessingJob
from clipforge.db.session import create_engine, create_session_factory
from clipforge.queue.repository import ACTIVE_STATUSES
from tests.clock import FakeClock
from tests.db import alembic_config, libpq_url, with_database
from tests.fixtures.make_media import make_media
from tests.jobs import Sessions

pytest_plugins = ["tests.pipeline"]

TEST_DATABASE_URL_ENV = "CLIPFORGE_TEST_DATABASE_URL"
SUPABASE_API_ROLES = ("anon", "authenticated")


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg's async mode cannot run on Windows' default Proactor loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


def emulate_supabase_api_roles(url: str) -> None:
    """Supabase grants its Data API roles everything created in `public` by default."""
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        for role in SUPABASE_API_ROLES:
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if not exists:
                with contextlib.suppress(psycopg.errors.DuplicateObject):
                    conn.execute(f"CREATE ROLE {role} NOLOGIN")
        roles = ", ".join(SUPABASE_API_ROLES)
        conn.execute(f"GRANT ALL ON SCHEMA public TO {roles}")
        for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            conn.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON {kind} TO {roles}"
            )


@pytest.fixture(scope="session")
def create_database() -> Iterator[Callable[[], str]]:
    """Creates empty throwaway databases on the test server; all are dropped at session end."""
    server_url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not server_url:
        if os.environ.get("CI"):
            pytest.fail(f"{TEST_DATABASE_URL_ENV} must be set in CI")
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set")
    created: list[str] = []

    def create() -> str:
        name = f"clipforge_test_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(libpq_url(server_url), autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        url = with_database(server_url, name)
        emulate_supabase_api_roles(url)
        return url

    yield create

    with psycopg.connect(libpq_url(server_url), autocommit=True) as conn:
        for name in created:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def migrated_database_url(create_database: Callable[[], str]) -> str:
    url = create_database()
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def db_conn(migrated_database_url: str) -> Iterator[psycopg.Connection]:
    """A connection whose work is rolled back after each test."""
    with psycopg.connect(libpq_url(migrated_database_url)) as conn:
        yield conn
        conn.rollback()


@pytest.fixture(scope="session")
def require_ffmpeg() -> None:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        if os.environ.get("CI"):
            pytest.fail("ffmpeg and ffprobe must be installed in CI")
        pytest.skip("ffmpeg/ffprobe are not installed")


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


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def valid_source(media_dir: Path, require_ffmpeg: None) -> Path:
    """A 31-minute source that passes validation (smallest valid duration plus a margin)."""
    return make_media(media_dir / "valid.mp4", duration_s=31 * 60)
