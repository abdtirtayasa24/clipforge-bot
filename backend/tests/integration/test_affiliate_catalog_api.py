import logging
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient

from tests.api import admin_client
from tests.db import alembic_config

PRODUCTS = "/api/v1/affiliate/products"
SECRET_TAG = "tag=secret-tracking-77"


@pytest.fixture(scope="module")
def database_url(create_database: Callable[[], str]) -> str:
    """The catalog and merchant allowlist are global, so this module has its own database."""
    url = create_database()
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    with admin_client(database_url) as test_client:
        yield test_client


def product(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Trail running shoes",
        "merchant": "Example Shop",
        "affiliate_url": f"https://shop.example.com/p/shoes?{SECRET_TAG}&utm_source=x",
        "destination_domain": "shop.example.com",
        "category": "sports",
        "tags": ["Running", "shoes"],
        "language_code": "id",
        "region_code": "ID",
        **overrides,
    }


def create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post(PRODUCTS, json=product(**overrides))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_create_list_and_update_a_product(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    created = create(client)

    assert created["affiliate_url"] == product()["affiliate_url"], "tracking params are kept"
    assert created["tags"] == ["running", "shoes"]
    assert created["active"] is True
    listed = client.get(PRODUCTS).json()["items"]
    assert created["id"] in [item["id"] for item in listed]
    updated = client.patch(f"{PRODUCTS}/{created['id']}", json={"name": "Road shoes"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Road shoes"
    assert updated.json()["affiliate_url"] == created["affiliate_url"]
    assert "secret-tracking-77" not in caplog.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"affiliate_url": "http://shop.example.com/p/1"},
        {"affiliate_url": "javascript:alert(1)"},
        {"affiliate_url": "https://10.0.0.1/p/1"},
        {"affiliate_url": "https://other-shop.example.org/p/1"},
        {"destination_domain": "10.0.0.1", "affiliate_url": "https://10.0.0.1/p"},
        {"destination_domain": "https://shop.example.com"},
        {"region_code": "ZZ"},
        {"name": ""},
        {"tags": ["x" * 51]},
        {"bogus": 1},
    ],
)
def test_invalid_products_are_422(client: TestClient, overrides: dict[str, Any]) -> None:
    response = client.post(PRODUCTS, json=product(**overrides))

    assert response.status_code == 422, response.text
    assert SECRET_TAG not in response.text


def test_merchant_allowlist_admits_tracking_redirect_hosts(client: TestClient) -> None:
    redirect = {"affiliate_url": "https://go.partner-network.net/r/123?sub=clip"}
    assert client.post(PRODUCTS, json=product(**redirect)).status_code == 422

    settings = client.patch(
        "/api/v1/settings", json={"affiliate_merchant_domains": ["*.partner-network.net"]}
    )
    assert settings.status_code == 200, settings.text

    assert client.post(PRODUCTS, json=product(**redirect)).status_code == 201


def test_patch_revalidates_the_url_against_the_new_domain(client: TestClient) -> None:
    created = create(client)

    response = client.patch(
        f"{PRODUCTS}/{created['id']}", json={"destination_domain": "elsewhere.example.org"}
    )
    explicit_null = client.patch(f"{PRODUCTS}/{created['id']}", json={"name": None})

    assert response.status_code == 422
    assert explicit_null.status_code == 422


def test_delete_deactivates_and_list_can_filter(client: TestClient) -> None:
    created = create(client, name=f"Lamp {uuid.uuid4().hex[:6]}")

    assert client.delete(f"{PRODUCTS}/{created['id']}").status_code == 204

    inactive = client.get(PRODUCTS, params={"active": "false"}).json()["items"]
    active = client.get(PRODUCTS, params={"active": "true"}).json()["items"]
    assert created["id"] in [item["id"] for item in inactive]
    assert created["id"] not in [item["id"] for item in active]
    assert [i["active"] for i in inactive if i["id"] == created["id"]] == [False]


def test_unknown_product_is_404(client: TestClient) -> None:
    assert client.patch(f"{PRODUCTS}/{uuid.uuid4()}", json={"name": "x"}).status_code == 404
    assert client.delete(f"{PRODUCTS}/{uuid.uuid4()}").status_code == 404


def test_catalog_requires_admin(database_url: str) -> None:
    with admin_client(database_url) as anonymous:
        anonymous.headers.pop("Authorization")
        assert anonymous.get(PRODUCTS).status_code == 401
        assert anonymous.post(PRODUCTS, json=product()).status_code == 401
