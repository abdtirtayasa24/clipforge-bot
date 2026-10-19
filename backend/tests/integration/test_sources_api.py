from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from clipforge.main import create_app
from tests.api import admin_client, api_settings, client_for

SOURCES = "/api/v1/sources"
CHANNEL_ID = "UCabcdefghijklmnopqrstuv"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def client(migrated_database_url: str, clean_discovery: None) -> Iterator[TestClient]:
    with admin_client(migrated_database_url) as test_client:
        yield test_client


def create(client: TestClient, **body: object) -> dict[str, object]:
    response = client.post(SOURCES, json=body)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def test_create_and_list_sources(client: TestClient) -> None:
    trending = create(client, kind="trending", name="Trending ID", region_code="ID")
    channel = create(client, kind="channel", name="Podcast", channel_id=CHANNEL_ID)

    assert trending["kind"] == "trending"
    assert trending["region_code"] == "ID"
    assert trending["enabled"] is True
    assert trending["last_run_at"] is None
    assert channel["channel_id"] == CHANNEL_ID
    listed = client.get(SOURCES).json()
    assert [source["id"] for source in listed["items"]] == [trending["id"], channel["id"]]


def test_duplicate_channel_is_a_conflict(client: TestClient) -> None:
    create(client, kind="channel", name="Podcast", channel_id=CHANNEL_ID)

    response = client.post(
        SOURCES, json={"kind": "channel", "name": "Again", "channel_id": CHANNEL_ID}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "channel", "name": "x", "channel_id": "UCshort"},
        {"kind": "channel", "name": "x", "channel_id": "XX" + CHANNEL_ID[2:]},
        {"kind": "channel", "name": "x", "channel_id": CHANNEL_ID + "a"},
        {"kind": "channel", "name": "x", "channel_id": "UCabcdefghijklmnopqrst<>"},
        {"kind": "channel", "name": "x"},
        {"kind": "trending", "name": "x", "channel_id": CHANNEL_ID},
        {"kind": "trending", "name": "x", "region_code": "id"},
        {"kind": "trending", "name": "x", "region_code": "IDN"},
        {"kind": "trending", "name": ""},
        {"kind": "trending", "name": "x" * 201},
        {"kind": "search", "name": "x"},
        {"kind": "trending", "name": "x", "unexpected": True},
    ],
)
def test_invalid_sources_are_rejected(client: TestClient, body: dict[str, object]) -> None:
    response = client.post(SOURCES, json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_update_source(client: TestClient) -> None:
    source = create(client, kind="trending", name="Trending", region_code="ID")

    response = client.patch(
        f"{SOURCES}/{source['id']}",
        json={"name": "Trending MY", "region_code": "MY", "enabled": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["region_code"], body["enabled"]) == ("Trending MY", "MY", False)


@pytest.mark.parametrize(
    "body", [{"region_code": "my"}, {"name": ""}, {"channel_id": CHANNEL_ID}, {"enabled": None}]
)
def test_invalid_updates_are_rejected(client: TestClient, body: dict[str, object]) -> None:
    source = create(client, kind="trending", name="Trending", region_code="ID")

    assert client.patch(f"{SOURCES}/{source['id']}", json=body).status_code == 422


def test_delete_disables_the_source(client: TestClient) -> None:
    source = create(client, kind="trending", name="Trending", region_code="ID")

    response = client.delete(f"{SOURCES}/{source['id']}")

    assert response.status_code == 204
    [listed] = client.get(SOURCES).json()["items"]
    assert (listed["id"], listed["enabled"]) == (source["id"], False)


def test_unknown_source_is_not_found(client: TestClient) -> None:
    assert client.patch(f"{SOURCES}/{UNKNOWN_ID}", json={"name": "x"}).status_code == 404
    assert client.delete(f"{SOURCES}/{UNKNOWN_ID}").status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", SOURCES),
        ("POST", SOURCES),
        ("PATCH", f"{SOURCES}/{UNKNOWN_ID}"),
        ("DELETE", f"{SOURCES}/{UNKNOWN_ID}"),
        ("GET", "/api/v1/videos"),
        ("POST", "/api/v1/discovery/run"),
    ],
)
def test_routes_require_an_admin_session(
    migrated_database_url: str, method: str, path: str
) -> None:
    with client_for(create_app(api_settings(migrated_database_url))) as anonymous:
        response = anonymous.request(method, path, json={})

    assert response.status_code == 401
