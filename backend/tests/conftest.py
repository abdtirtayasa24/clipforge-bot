import asyncio
import contextlib
import os
import sys
import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from alembic import command

from tests.db import alembic_config, libpq_url, with_database

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
