import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from tests.api import admin_client
from tests.db import libpq_url

VIDEOS = "/api/v1/videos"
DISCOVERED = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    with admin_client(migrated_database_url) as test_client:
        yield test_client


@pytest.fixture
def catalog(migrated_database_url: str) -> Iterator[dict[str, str]]:
    """Videos a, b, c (newest first) linked to a private source; only b has ready media."""
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        source = conn.execute(
            "INSERT INTO youtube_sources (kind, name, region_code) "
            "VALUES ('trending', 'Videos API', 'ID') RETURNING id"
        ).fetchone()
        assert source is not None
        ids = {"source": str(source[0])}
        for age_days, (name, rights) in enumerate(
            [("a", "unverified"), ("b", "authorized"), ("c", "rejected")]
        ):
            row = conn.execute(
                "INSERT INTO videos (youtube_video_id, title, channel_id, eligibility, "
                "rights_status, duration_seconds, discovered_at) "
                "VALUES (%s, %s, 'UCx', 'eligible', %s, 3600, %s) RETURNING id",
                (
                    uuid.uuid4().hex[:11],
                    f"Video {name}",
                    rights,
                    DISCOVERED - timedelta(days=age_days),
                ),
            ).fetchone()
            assert row is not None
            ids[name] = str(row[0])
            conn.execute(
                "INSERT INTO video_sources (video_id, source_id) VALUES (%s, %s)",
                (row[0], source[0]),
            )
        conn.execute(
            "INSERT INTO media_assets (video_id, kind, permission_evidence, state) "
            "VALUES (%s, 'upload', %s, 'ready')",
            (ids["b"], Jsonb({"type": "creator_permission"})),
        )
        yield ids
        conn.execute("DELETE FROM youtube_sources WHERE id = %s", (source[0],))


def titles(client: TestClient, **params: object) -> list[str]:
    response = client.get(VIDEOS, params=params)
    assert response.status_code == 200, response.text
    return [item["title"] for item in response.json()["items"]]


def test_lists_videos_of_a_source_newest_first(client: TestClient, catalog: dict[str, str]) -> None:
    response = client.get(VIDEOS, params={"source_id": catalog["source"]})

    body = response.json()
    assert [item["title"] for item in body["items"]] == ["Video a", "Video b", "Video c"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 20, 0)
    first = body["items"][0]
    assert first["id"] == catalog["a"]
    assert first["duration_seconds"] == 3600
    assert first["rights_status"] == "unverified"
    assert first["source_status"] == "awaiting_source"


def test_source_status_reflects_ready_media(client: TestClient, catalog: dict[str, str]) -> None:
    source = catalog["source"]

    assert titles(client, source_id=source, source_status="ready") == ["Video b"]
    assert titles(client, source_id=source, source_status="awaiting_source") == [
        "Video a",
        "Video c",
    ]


def test_filters_by_rights_and_eligibility(client: TestClient, catalog: dict[str, str]) -> None:
    source = catalog["source"]

    assert titles(client, source_id=source, rights_status="rejected") == ["Video c"]
    assert titles(client, source_id=source, eligibility="ineligible") == []


def test_pagination(client: TestClient, catalog: dict[str, str]) -> None:
    response = client.get(VIDEOS, params={"source_id": catalog["source"], "limit": 2, "offset": 1})

    body = response.json()
    assert [item["title"] for item in body["items"]] == ["Video b", "Video c"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 2, 1)


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"rights_status": "maybe"},
        {"source_status": "maybe"},
        {"source_id": "x"},
    ],
)
def test_invalid_query_is_rejected(client: TestClient, params: dict[str, object]) -> None:
    assert client.get(VIDEOS, params=params).status_code == 422


def test_video_detail_includes_source_status(client: TestClient, catalog: dict[str, str]) -> None:
    response = client.get(f"{VIDEOS}/{catalog['b']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["title"]) == (catalog["b"], "Video b")
    assert (body["rights_status"], body["source_status"]) == ("authorized", "ready")


def test_unknown_video_detail_is_not_found(client: TestClient) -> None:
    assert client.get(f"{VIDEOS}/{uuid.uuid4()}").status_code == 404
