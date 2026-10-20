import base64
import hashlib
import json
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from clipforge.security.crypto import TokenCipher
from clipforge.storage.drive_oauth import ABOUT_URL, AUTH_URL, DRIVE_SCOPE, STATE_KEY, TOKEN_URL
from tests.api import admin_client
from tests.db import libpq_url

CONNECT = "/api/v1/integrations/drive/connect"
CALLBACK = "/api/v1/integrations/drive/callback"
STATUS = "/api/v1/integrations/drive/status"
REDIRECT_URI = "https://clipforge.example/api/v1/integrations/drive/callback"
ENCRYPTION_KEY = Fernet.generate_key().decode()
REFRESH_TOKEN = "1//refresh-token-plaintext"
ACCESS_TOKEN = "ya29.access-token-plaintext"


def execute(url: str, sql: str, *params: object) -> list[tuple[object, ...]]:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


@pytest.fixture
def clean_drive(migrated_database_url: str) -> Iterator[None]:
    def reset() -> None:
        execute(migrated_database_url, "DELETE FROM oauth_credentials")
        execute(migrated_database_url, "DELETE FROM app_settings WHERE key = %s", STATE_KEY)

    reset()
    yield
    reset()


@pytest.fixture
def client(migrated_database_url: str, clean_drive: None) -> Iterator[TestClient]:
    with admin_client(
        migrated_database_url,
        google_client_id="client-id.apps.googleusercontent.com",
        google_client_secret="client-secret",
        drive_redirect_uri=REDIRECT_URI,
        token_encryption_key=ENCRYPTION_KEY,
    ) as test_client:
        yield test_client


@pytest.fixture
def google() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(ABOUT_URL).respond(json={"user": {"emailAddress": "admin@example.com"}})
        yield router


def token_exchange(router: respx.MockRouter, **overrides: object) -> respx.Route:
    body = {
        "access_token": ACCESS_TOKEN,
        "expires_in": 3599,
        "refresh_token": REFRESH_TOKEN,
        "scope": DRIVE_SCOPE,
        "token_type": "Bearer",
    } | overrides
    return router.post(TOKEN_URL).respond(json=body)


def start(client: TestClient) -> dict[str, str]:
    response = client.get(CONNECT)
    assert response.status_code == 200
    url = urlsplit(response.json()["auth_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == AUTH_URL
    return {key: values[0] for key, values in parse_qs(url.query).items()}


def callback(client: TestClient, **params: str) -> httpx.Response:
    return client.get(CALLBACK, params=params, headers={"Authorization": ""})


def test_connect_requires_an_admin(client: TestClient) -> None:
    assert client.get(CONNECT, headers={"Authorization": ""}).status_code == 401
    assert client.get(STATUS, headers={"Authorization": ""}).status_code == 401


def test_connect_builds_an_offline_drive_file_consent_url(client: TestClient) -> None:
    query = start(client)

    assert query["client_id"] == "client-id.apps.googleusercontent.com"
    assert query["redirect_uri"] == REDIRECT_URI
    assert query["scope"] == DRIVE_SCOPE
    assert (query["response_type"], query["access_type"], query["prompt"]) == (
        "code",
        "offline",
        "consent",
    )
    assert query["code_challenge_method"] == "S256"
    assert len(query["state"]) >= 32


def test_callback_stores_the_refresh_token_encrypted(
    client: TestClient, google: respx.MockRouter, migrated_database_url: str
) -> None:
    exchange = token_exchange(google)
    query = start(client)

    response = callback(client, state=query["state"], code="auth-code")

    assert response.status_code == 200
    sent = parse_qs(exchange.calls.last.request.content.decode())
    assert sent["code"] == ["auth-code"]
    assert sent["redirect_uri"] == [REDIRECT_URI]
    verifier = sent["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge.decode() == query["code_challenge"]
    [(stored, scopes, email)] = execute(
        migrated_database_url,
        "SELECT encrypted_refresh_token, scopes, account_email FROM oauth_credentials "
        "WHERE provider = 'google_drive'",
    )
    assert isinstance(stored, str)
    assert REFRESH_TOKEN not in stored
    assert TokenCipher(ENCRYPTION_KEY).decrypt(stored) == REFRESH_TOKEN
    assert (scopes, email) == ([DRIVE_SCOPE], "admin@example.com")


def test_reused_state_is_rejected(client: TestClient, google: respx.MockRouter) -> None:
    token_exchange(google)
    state = start(client)["state"]
    assert callback(client, state=state, code="auth-code").status_code == 200

    assert callback(client, state=state, code="auth-code").status_code == 400


def test_unknown_or_expired_state_is_rejected(
    client: TestClient, google: respx.MockRouter, migrated_database_url: str
) -> None:
    exchange = token_exchange(google)
    state = start(client)["state"]

    assert callback(client, state="forged-state", code="auth-code").status_code == 400
    assert callback(client, code="auth-code").status_code == 400
    execute(
        migrated_database_url,
        "UPDATE app_settings SET value = jsonb_set(value, '{expires_at}', "
        "to_jsonb(extract(epoch FROM now()) - 60)) WHERE key = %s",
        STATE_KEY,
    )
    assert callback(client, state=state, code="auth-code").status_code == 400
    assert not exchange.called


def test_consent_denied_or_missing_scope_is_rejected(
    client: TestClient, google: respx.MockRouter, migrated_database_url: str
) -> None:
    state = start(client)["state"]
    assert callback(client, state=state, error="access_denied").status_code == 400

    token_exchange(google, scope="openid")
    state = start(client)["state"]
    assert callback(client, state=state, code="auth-code").status_code == 400
    assert execute(migrated_database_url, "SELECT 1 FROM oauth_credentials") == []


def test_status_reports_the_connection_without_token_material(
    client: TestClient, google: respx.MockRouter
) -> None:
    assert client.get(STATUS).json()["status"] == "disconnected"
    token_exchange(google)
    assert callback(client, state=start(client)["state"], code="auth-code").status_code == 200

    response = client.get(STATUS)

    body = response.json()
    assert body["status"] == "connected"
    assert body["scopes"] == [DRIVE_SCOPE]
    assert body["account_email"] == "admin@example.com"
    assert "target_folder_name" in body
    text = json.dumps(body)
    for secret in (REFRESH_TOKEN, ACCESS_TOKEN, "client-secret", ENCRYPTION_KEY):
        assert secret not in text
    assert not any("token" in key for key in body)


def test_status_reports_a_revoked_refresh_token_as_expired(
    client: TestClient, google: respx.MockRouter
) -> None:
    token_exchange(google)
    assert callback(client, state=start(client)["state"], code="auth-code").status_code == 200
    google.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})

    assert client.get(STATUS).json()["status"] == "expired"
