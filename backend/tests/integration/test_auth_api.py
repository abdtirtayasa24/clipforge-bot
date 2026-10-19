import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from clipforge.auth.deps import CurrentAdmin
from clipforge.auth.session import issue_session_token
from clipforge.logging import JsonFormatter
from clipforge.main import create_app
from tests.api import SESSION_SECRET, api_settings, client_for
from tests.db import libpq_url
from tests.telegram import sign_init_data

AUTH_URL = "/api/v1/auth/telegram"
PRIVATE_URL = "/api/v1/_test/private"


@pytest.fixture
def admin(migrated_database_url: str) -> Iterator[tuple[uuid.UUID, int]]:
    telegram_user_id = int(uuid.uuid4().int % 10**9) + 1
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO admin_users (telegram_user_id) VALUES (%s) RETURNING id",
            (telegram_user_id,),
        ).fetchone()
        assert row is not None
        yield row[0], telegram_user_id
        conn.execute("DELETE FROM admin_users WHERE id = %s", (row[0],))


def set_admin_active(url: str, admin_id: uuid.UUID, active: bool) -> None:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        conn.execute("UPDATE admin_users SET active = %s WHERE id = %s", (active, admin_id))


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    app = create_app(api_settings(migrated_database_url, auth_max_failures=5))

    @app.get(PRIVATE_URL)
    async def private(current: CurrentAdmin) -> dict[str, str]:
        return {"admin_user_id": str(current.id)}

    with client_for(app) as test_client:
        yield test_client


def login(client: TestClient, init_data: str) -> tuple[int, dict[str, object]]:
    response = client.post(AUTH_URL, json={"init_data": init_data})
    return response.status_code, response.json()


def now() -> datetime:
    return datetime.now(UTC)


def test_admin_login_returns_session_token(
    client: TestClient, admin: tuple[uuid.UUID, int]
) -> None:
    admin_id, telegram_user_id = admin

    status, body = login(client, sign_init_data(user_id=telegram_user_id, auth_date=now()))

    assert status == 200
    assert body["token_type"] == "bearer"
    assert isinstance(body["expires_at"], str)
    private = client.get(PRIVATE_URL, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert private.status_code == 200
    assert private.json() == {"admin_user_id": str(admin_id)}


def test_tampered_init_data_is_unauthorized(
    client: TestClient, admin: tuple[uuid.UUID, int]
) -> None:
    init_data = sign_init_data(user_id=admin[1], auth_date=now())

    status, _ = login(client, init_data.replace("first_name", "first_nam3"))

    assert status == 401


def test_stale_init_data_is_unauthorized(client: TestClient, admin: tuple[uuid.UUID, int]) -> None:
    status, _ = login(client, sign_init_data(user_id=admin[1], auth_date=now() - timedelta(days=2)))

    assert status == 401


def test_non_admin_is_forbidden(client: TestClient) -> None:
    status, _ = login(client, sign_init_data(user_id=777_000_001, auth_date=now()))

    assert status == 403


def test_inactive_admin_is_forbidden(
    client: TestClient, admin: tuple[uuid.UUID, int], migrated_database_url: str
) -> None:
    set_admin_active(migrated_database_url, admin[0], False)

    status, _ = login(client, sign_init_data(user_id=admin[1], auth_date=now()))

    assert status == 403


def test_request_body_must_contain_init_data(client: TestClient) -> None:
    assert client.post(AUTH_URL, json={}).status_code == 422


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer", "Bearer not-a-token", "Basic abc", "Bearer v1.e30.e30"],
)
def test_private_route_rejects_missing_or_forged_token(
    client: TestClient, authorization: str | None
) -> None:
    headers = {"Authorization": authorization} if authorization else {}

    response = client.get(PRIVATE_URL, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_private_route_rejects_expired_token(
    client: TestClient, admin: tuple[uuid.UUID, int]
) -> None:
    token, _ = issue_session_token(
        admin[0], admin[1], SESSION_SECRET, now=now() - timedelta(hours=13)
    )

    response = client.get(PRIVATE_URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_private_route_rejects_token_of_deactivated_admin(
    client: TestClient, admin: tuple[uuid.UUID, int], migrated_database_url: str
) -> None:
    token, _ = issue_session_token(admin[0], admin[1], SESSION_SECRET, now=now())
    set_admin_active(migrated_database_url, admin[0], False)

    response = client.get(PRIVATE_URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_repeated_failures_are_rate_limited(client: TestClient) -> None:
    bad = sign_init_data(user_id=1, auth_date=now() - timedelta(days=2))
    statuses = [login(client, bad)[0] for _ in range(6)]

    assert statuses == [401] * 5 + [429]


def test_init_data_and_tokens_never_logged(
    client: TestClient, admin: tuple[uuid.UUID, int], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    good = sign_init_data(user_id=admin[1], auth_date=now())
    stale = sign_init_data(user_id=admin[1], auth_date=now() - timedelta(days=2))

    _, body = login(client, good)
    login(client, stale)
    client.get(PRIVATE_URL, headers={"Authorization": f"Bearer {body['access_token']}"})
    client.get(PRIVATE_URL, headers={"Authorization": "Bearer forged.token.value"})

    formatter = JsonFormatter()
    raw = "\n".join(str(vars(record)) for record in caplog.records)
    formatted = "\n".join(formatter.format(record) for record in caplog.records)
    assert caplog.records, "expected auth events to be logged"
    for secret in (good, stale, str(body["access_token"]), "forged.token.value"):
        assert secret not in raw
        assert secret not in formatted
