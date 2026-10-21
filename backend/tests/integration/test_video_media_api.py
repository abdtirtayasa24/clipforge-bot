import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from tests.api import admin_client
from tests.db import libpq_url

SOURCE_URL = "https://media.example.com/videos/talk.mp4?sig=abc"


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    with admin_client(migrated_database_url) as test_client:
        yield test_client


@pytest.fixture
def video_id(migrated_database_url: str) -> str:
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO videos (youtube_video_id, title, channel_id) VALUES (%s, 'v', 'UCx') "
            "RETURNING id",
            (uuid.uuid4().hex[:11],),
        ).fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture
def allowlist(migrated_database_url: str) -> Iterator[None]:
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        conn.execute(
            "UPDATE app_settings SET value = %s WHERE key = 'permitted_download_domains'",
            (Jsonb(["media.example.com", "*.cdn.example.com"]),),
        )
        yield
        conn.execute(
            "UPDATE app_settings SET value = '[]' WHERE key = 'permitted_download_domains'"
        )


def register(client: TestClient, video_id: str, **overrides: object) -> tuple[int, Any]:
    body = {"url": SOURCE_URL} | overrides
    response = client.post(f"/api/v1/videos/{video_id}/media", json=body)
    return response.status_code, response.json()


@pytest.mark.usefixtures("allowlist")
def test_permitted_url_is_registered_with_its_registrar(
    client: TestClient, migrated_database_url: str, video_id: str
) -> None:
    status, body = register(client, video_id)

    assert status == 201
    assert (body["kind"], body["state"], body["source_url"]) == ("url", "pending", SOURCE_URL)
    with psycopg.connect(libpq_url(migrated_database_url)) as conn:
        row = conn.execute(
            "SELECT m.permission_evidence, v.rights_status FROM media_assets m "
            "JOIN videos v ON v.id = m.video_id WHERE m.id = %s",
            (body["id"],),
        ).fetchone()
    assert row is not None
    evidence, rights = row
    assert set(evidence) == {"recorded_by", "recorded_at"}
    assert rights == "unverified"


@pytest.mark.usefixtures("allowlist")
@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.com/talk.mp4",
        "file:///etc/passwd",
        "https://127.0.0.1/talk.mp4",
        "https://192.168.1.10/talk.mp4",
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://other.example.com/talk.mp4",
        "https://cdn.example.com.evil.net/talk.mp4",
    ],
)
def test_unpermitted_urls_are_refused(client: TestClient, video_id: str, url: str) -> None:
    status, body = register(client, video_id, url=url)

    assert status == 422
    assert body["error"]["code"] == "url_rejected"


def test_url_ingestion_is_disabled_without_an_allowlist(client: TestClient, video_id: str) -> None:
    status, body = register(client, video_id)

    assert status == 422
    assert "disabled" in body["error"]["message"]


@pytest.mark.usefixtures("allowlist")
@pytest.mark.parametrize(
    "overrides",
    [{"url": ""}, {"url": "x" * 2049}],
)
def test_url_length_is_validated(
    client: TestClient, video_id: str, overrides: dict[str, object]
) -> None:
    status, _ = register(client, video_id, **overrides)

    assert status == 422


@pytest.mark.usefixtures("allowlist")
def test_unknown_video_is_not_found(client: TestClient) -> None:
    status, _ = register(client, str(uuid.uuid4()))

    assert status == 404
