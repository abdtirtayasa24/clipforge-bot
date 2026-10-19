import asyncio
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipforge.auth.session import issue_session_token
from clipforge.config import Settings
from clipforge.main import create_app
from tests.db import libpq_url
from tests.telegram import BOT_TOKEN

SESSION_SECRET = "test-session-secret-0123456789abcdef"


def api_settings(database_url: str | None = None, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": database_url,
        "telegram_bot_token": BOT_TOKEN,
        "session_secret": SESSION_SECRET,
        "db_pool_size": 2,
    }
    return Settings.model_validate(values | overrides)


def client_for(app: FastAPI) -> TestClient:
    # psycopg's async mode cannot run on Windows' default Proactor loop.
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    return TestClient(app, backend_options=options)


def admin_token(database_url: str) -> str:
    telegram_user_id = int(uuid.uuid4().int % 10**9) + 1
    with psycopg.connect(libpq_url(database_url), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO admin_users (telegram_user_id) VALUES (%s) RETURNING id",
            (telegram_user_id,),
        ).fetchone()
    assert row is not None
    token, _ = issue_session_token(row[0], telegram_user_id, SESSION_SECRET, now=datetime.now(UTC))
    return token


@contextmanager
def admin_client(database_url: str, **overrides: object) -> Iterator[TestClient]:
    """A client for the full app, authenticated as a freshly created administrator."""
    app = create_app(api_settings(database_url, **overrides))
    with client_for(app) as client:
        client.headers["Authorization"] = f"Bearer {admin_token(database_url)}"
        yield client
