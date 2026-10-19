import asyncio
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipforge.config import Settings
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
