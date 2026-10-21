"""Log audit (ADR-022): every line the app writes during login, Drive OAuth and a full pipeline
run, formatted exactly as journald receives it, is free of credentials."""

import io
import logging
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy import text

from clipforge.logging import JsonFormatter, install_context_record_factory
from clipforge.main import create_app
from clipforge.queue.errors import TransientError
from clipforge.security.crypto import TokenCipher
from clipforge.storage.drive_oauth import (
    ABOUT_URL,
    DRIVE_SCOPE,
    STATE_KEY,
    TOKEN_URL,
    DriveTokens,
    OAuthClient,
)
from tests.api import SESSION_SECRET, api_settings, client_for
from tests.db import libpq_url
from tests.jobs import claim, load_job
from tests.pipeline import BOT_TOKEN as PIPELINE_BOT_TOKEN
from tests.pipeline import Pipeline
from tests.telegram import BOT_TOKEN, sign_init_data

pytestmark = pytest.mark.security

CLIENT_ID = "client-id.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-audit-client-secret"
REDIRECT_URI = "https://clipforge.example/api/v1/integrations/drive/callback"
ENCRYPTION_KEY = Fernet.generate_key().decode()
AUTH_CODE = "4/0audit-authorization-code"
REFRESH_TOKEN = "1//0audit-refresh-token"
ACCESS_TOKEN = "ya29.audit-access-token"
GEMINI_KEY = "AIzaSyAudit-gemini-api-key"
FORGED_BEARER = "v1.forged-payload.forged-signature"
OAUTH_INSERT = text(
    "INSERT INTO oauth_credentials (provider, encrypted_refresh_token, scopes) "
    "VALUES ('google_drive', :token, :scopes)"
)


@pytest.fixture
def journal() -> Iterator[Callable[[], list[str]]]:
    """The lines the production JSON handler would write, at every level."""
    install_context_record_factory()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield lambda: stream.getvalue().splitlines()
    root.removeHandler(handler)
    root.setLevel(level)


@pytest.fixture
def drive_credentials(migrated_database_url: str) -> Iterator[None]:
    def reset() -> None:
        with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
            conn.execute("DELETE FROM oauth_credentials")
            conn.execute("DELETE FROM app_settings WHERE key = %s", (STATE_KEY,))

    reset()
    yield
    reset()


def assert_absent(lines: list[str], secrets: list[str]) -> None:
    assert lines, "expected the scenario to log"
    leaks = [(secret, line) for line in lines for secret in secrets if secret in line]
    assert leaks == []


@pytest.mark.usefixtures("drive_credentials")
def test_login_api_calls_and_drive_oauth_log_no_secrets(
    migrated_database_url: str, journal: Callable[[], list[str]]
) -> None:
    telegram_user_id = int(uuid.uuid4().int % 10**9) + 1
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        conn.execute("INSERT INTO admin_users (telegram_user_id) VALUES (%s)", (telegram_user_id,))
    now = datetime.now(UTC)
    good = sign_init_data(user_id=telegram_user_id, auth_date=now)
    stale = sign_init_data(user_id=telegram_user_id, auth_date=now - timedelta(days=2))
    tampered = good.replace("Admin", "Mallory")
    settings = api_settings(
        migrated_database_url,
        google_client_id=CLIENT_ID,
        google_client_secret=CLIENT_SECRET,
        drive_redirect_uri=REDIRECT_URI,
        token_encryption_key=ENCRYPTION_KEY,
    )

    with (
        client_for(create_app(settings)) as client,
        respx.mock(assert_all_called=False) as google,
    ):
        for rejected in (stale, tampered):
            assert client.post("/api/v1/auth/telegram", json={"init_data": rejected}).is_error
        login = client.post("/api/v1/auth/telegram", json={"init_data": good})
        token = login.json()["access_token"]
        bearer = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/v1/jobs", headers=bearer).status_code == 200
        forged = {"Authorization": f"Bearer {FORGED_BEARER}"}
        assert client.get("/api/v1/jobs", headers=forged).status_code == 401

        connect = client.get("/api/v1/integrations/drive/connect", headers=bearer)
        state = parse_qs(urlsplit(connect.json()["auth_url"]).query)["state"][0]
        google.post(TOKEN_URL).respond(
            json={
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "expires_in": 3599,
                "scope": DRIVE_SCOPE,
                "token_type": "Bearer",
            }
        )
        google.get(ABOUT_URL).respond(json={"user": {"emailAddress": "admin@example.com"}})
        callback = "/api/v1/integrations/drive/callback"
        assert client.get(callback, params={"state": state, "code": AUTH_CODE}).status_code == 200
        assert client.get(callback, params={"state": state, "code": AUTH_CODE}).status_code == 400
        assert client.get("/api/v1/integrations/drive/status", headers=bearer).status_code == 200

    lines = journal()
    assert any("admin authenticated" in line for line in lines)
    assert any("google drive connected" in line for line in lines)
    assert_absent(
        lines,
        [
            good,
            stale,
            tampered,
            parse_qs(good)["hash"][0],
            token,
            FORGED_BEARER,
            BOT_TOKEN,
            SESSION_SECRET,
            CLIENT_SECRET,
            ENCRYPTION_KEY,
            AUTH_CODE,
            state,
            ACCESS_TOKEN,
            REFRESH_TOKEN,
        ],
    )


async def test_pipeline_run_with_drive_and_notifications_logs_no_secrets(
    pipeline: Pipeline, journal: Callable[[], list[str]], drive_credentials: None
) -> None:
    cipher = TokenCipher(ENCRYPTION_KEY)
    async with pipeline.sessions.begin() as session:
        await session.execute(
            OAUTH_INSERT, {"token": cipher.encrypt(REFRESH_TOKEN), "scopes": [DRIVE_SCOPE]}
        )
    pipeline.router.post(TOKEN_URL).respond(
        json={"access_token": ACCESS_TOKEN, "expires_in": 3599, "token_type": "Bearer"}
    )
    tokens = DriveTokens(
        pipeline.sessions,
        pipeline.http,
        OAuthClient(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI),
        cipher,
        clock=pipeline.clock,
    )
    job_id = await pipeline.enqueue()
    # Older provider clients put the key in the URL; the error text must not carry it to logs.
    pipeline.transcription.failures = [
        TransientError(
            "POST https://generativelanguage.googleapis.com/v1beta/models/m:generateContent"
            f"?key={GEMINI_KEY} timed out"
        )
    ]
    runner = pipeline.runner(pipeline.stages(drive=pipeline.drive_client(tokens)))

    assert await runner.run(await claim(pipeline.queue)) == "retry_scheduled"
    assert GEMINI_KEY not in str((await load_job(pipeline.sessions, job_id)).last_error)
    pipeline.clock.advance(timedelta(days=1))
    assert await runner.run(await claim(pipeline.queue)) == "completed"

    lines = journal()
    assert any("clip uploaded" in line for line in lines)
    assert pipeline.telegram.called
    secrets = [PIPELINE_BOT_TOKEN, REFRESH_TOKEN, ACCESS_TOKEN, CLIENT_SECRET, GEMINI_KEY]
    assert_absent(lines, secrets)
