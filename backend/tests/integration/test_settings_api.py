from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient

from tests.api import admin_client
from tests.db import alembic_config, libpq_url

SETTINGS = "/api/v1/settings"


@pytest.fixture
def database_url(create_database: Callable[[], str]) -> str:
    """Settings are global, so each test gets its own database."""
    url = create_database()
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    with admin_client(database_url) as test_client:
        yield test_client


def query(url: str, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


def events(url: str) -> list[tuple[Any, ...]]:
    return query(
        url,
        "SELECT setting_key, old_value, new_value, admin_user_id IS NOT NULL "
        "FROM app_setting_events ORDER BY id",
    )


def provider_id(url: str, model: str) -> str:
    return str(query(url, "SELECT id FROM ai_provider_configs WHERE model = %s", model)[0][0])


def test_get_returns_the_stored_settings(client: TestClient) -> None:
    response = client.get(SETTINGS)

    assert response.status_code == 200
    body = response.json()
    assert (body["min_score"], body["clip_min_s"], body["clip_max_s"]) == (75, 20, 60)
    assert body["affiliate_links_enabled"] is False
    assert body["discovery"]["region_code"] == "ID"
    assert body["models"]["analysis"] == "gemini-3.5-flash-lite"


def test_patch_updates_values_merges_nested_objects_and_audits(
    client: TestClient, database_url: str
) -> None:
    response = client.patch(
        SETTINGS,
        json={"min_score": 80, "discovery": {"region_code": "US"}, "max_clips": 3},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["min_score"] == 80
    assert body["discovery"]["region_code"] == "US"
    assert body["discovery"]["top_categories"] == 5
    assert client.get(SETTINGS).json()["min_score"] == 80
    audited = {key: (old, new, by_admin) for key, old, new, by_admin in events(database_url)}
    assert audited["min_score"] == (75, 80, True)
    old_discovery, new_discovery, _ = audited["discovery"]
    assert (old_discovery["region_code"], new_discovery["region_code"]) == ("ID", "US")
    assert "max_clips" not in audited, "unchanged values are not audited"


@pytest.mark.parametrize(
    "body",
    [
        {"bogus": 1},
        {"discovery": {"bogus": 1}},
        {"models": {"bogus": "x"}},
        {"clip_min_s": 50, "clip_max_s": 40},
        {"clip_min_s": 10},
        {"clip_max_s": 90},
        {"max_clips": 0},
        {"max_clips": 4},
        {"min_score": 101},
        {"min_score": -5},
        {"max_overlap": 0.5},
        {"min_score": None},
        {"discovery": {"region_code": "ZZ"}},
        {"permitted_download_domains": ["https://cdn.example.com/path"]},
        {"permitted_download_domains": ["192.168.1.10"]},
        {"permitted_download_domains": ["*.googlevideo.com"]},
        {"models": {"analysis": "no-such-model"}},
        {"models": {"transcription": "gemini-3.5-flash-lite"}},
    ],
)
def test_invalid_patch_is_422_and_changes_nothing(
    client: TestClient, database_url: str, body: dict[str, Any]
) -> None:
    response = client.patch(SETTINGS, json=body)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert events(database_url) == []
    assert client.get(SETTINGS).json()["min_score"] == 75


def test_disabled_model_cannot_be_selected(client: TestClient, database_url: str) -> None:
    query(
        database_url,
        "INSERT INTO ai_provider_configs (provider, model, capability, secret_ref, enabled) "
        "VALUES ('gemini', 'gemini-old', 'analysis', 'GEMINI_API_KEY', false)",
    )

    response = client.patch(SETTINGS, json={"models": {"analysis": "gemini-old"}})

    assert response.status_code == 422


def test_enabled_model_selection_and_null_fallback_are_accepted(client: TestClient) -> None:
    response = client.patch(
        SETTINGS,
        json={"models": {"analysis": "gemini:gemini-3-flash-preview", "analysis_fallback": None}},
    )

    assert response.status_code == 422, "a rerank-only model is not an analysis model"
    response = client.patch(SETTINGS, json={"models": {"analysis_fallback": None}})
    assert response.status_code == 200, response.text
    assert response.json()["models"]["analysis_fallback"] is None
    assert response.json()["models"]["analysis"] == "gemini-3.5-flash-lite"


def test_permitted_domains_are_normalized(client: TestClient) -> None:
    response = client.patch(
        SETTINGS, json={"permitted_download_domains": ["CDN.Example.com", "*.files.example.org"]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["permitted_download_domains"] == [
        "cdn.example.com",
        "*.files.example.org",
    ]


def test_provider_prices_and_quotas_are_editable_and_audited(
    client: TestClient, database_url: str
) -> None:
    analysis = provider_id(database_url, "gemini-3.5-flash-lite")

    response = client.patch(
        SETTINGS,
        json={
            "providers": [
                {
                    "id": analysis,
                    "price_input_per_mtok": "0.10",
                    "price_output_per_mtok": "0.40",
                    "rpm_limit": 7,
                    "rpd_limit": None,
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    listed = {item["id"]: item for item in client.get("/api/v1/ai/providers").json()["items"]}
    assert listed[analysis]["prices"]["input_per_mtok"] == "0.1"
    assert listed[analysis]["prices"]["output_per_mtok"] == "0.4"
    assert listed[analysis]["quota"]["rpm_limit"] == 7
    assert listed[analysis]["quota"]["rpd_limit"] is None
    [(key, old, new, by_admin)] = events(database_url)
    assert key == f"ai_provider_configs/{analysis}"
    assert old == {
        "price_input_per_mtok": "0",
        "price_output_per_mtok": "0",
        "rpm_limit": 10,
        "rpd_limit": 200,
    }
    assert new == {
        "price_input_per_mtok": "0.1",
        "price_output_per_mtok": "0.4",
        "rpm_limit": 7,
        "rpd_limit": None,
    }
    assert by_admin


@pytest.mark.parametrize(
    "change",
    [
        {"price_input_per_mtok": "-1"},
        {"rpm_limit": 0},
        {"currency": "usd"},
        {"bogus": 1},
    ],
)
def test_invalid_provider_changes_are_422(
    client: TestClient, database_url: str, change: dict[str, Any]
) -> None:
    analysis = provider_id(database_url, "gemini-3.5-flash-lite")

    response = client.patch(SETTINGS, json={"providers": [{"id": analysis, **change}]})

    assert response.status_code == 422


def test_unknown_provider_or_disabling_a_selected_model_is_422(
    client: TestClient, database_url: str
) -> None:
    unknown = client.patch(
        SETTINGS, json={"providers": [{"id": "00000000-0000-0000-0000-000000000000"}]}
    )
    analysis = provider_id(database_url, "gemini-3.5-flash-lite")
    disabled = client.patch(SETTINGS, json={"providers": [{"id": analysis, "enabled": False}]})

    assert unknown.status_code == 422
    assert disabled.status_code == 422
    assert query(database_url, "SELECT enabled FROM ai_provider_configs WHERE id = %s", analysis)[
        0
    ] == (True,)


def test_settings_require_admin(database_url: str) -> None:
    with admin_client(database_url) as anonymous:
        anonymous.headers.pop("Authorization")
        assert anonymous.get(SETTINGS).status_code == 401
        assert anonymous.patch(SETTINGS, json={"min_score": 80}).status_code == 401


def test_title_overlay_is_on_by_default_and_can_be_turned_off(
    client: TestClient, database_url: str
) -> None:
    assert client.get(SETTINGS).json()["title_overlay_enabled"] is True

    body = client.patch(SETTINGS, json={"title_overlay_enabled": False}).json()

    assert body["title_overlay_enabled"] is False
    assert query(
        database_url, "SELECT value FROM app_settings WHERE key = 'title_overlay_enabled'"
    ) == [(False,)]


def test_daily_job_cap_is_off_by_default_and_can_be_set_or_cleared(
    client: TestClient, database_url: str
) -> None:
    assert client.get(SETTINGS).json()["daily_job_cap"] is None

    assert client.patch(SETTINGS, json={"daily_job_cap": 10}).json()["daily_job_cap"] == 10
    assert query(database_url, "SELECT value FROM app_settings WHERE key = 'daily_job_cap'") == [
        (10,)
    ]
    assert client.patch(SETTINGS, json={"daily_job_cap": 0}).status_code == 422
    assert client.patch(SETTINGS, json={"daily_job_cap": None}).json()["daily_job_cap"] is None
