import asyncio
import os
import sys
import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from alembic import command

from tests.db import alembic_config, libpq_url, with_database

TEST_DATABASE_URL_ENV = "CLIPFORGE_TEST_DATABASE_URL"


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg's async mode cannot run on Windows' default Proactor loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(scope="session")
def create_database() -> Iterator[Callable[[], str]]:
    """Creates empty throwaway databases on the test server; all are dropped at session end."""
    server_url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not server_url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set")
    created: list[str] = []

    def create() -> str:
        name = f"clipforge_test_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(libpq_url(server_url), autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        return with_database(server_url, name)

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
