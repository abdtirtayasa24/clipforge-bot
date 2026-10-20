import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from tests.api import admin_client
from tests.db import alembic_config, libpq_url

DISCLOSURE = "Affiliate link — I may earn a commission from qualifying purchases."


@pytest.fixture
def database_url(create_database: Callable[[], str]) -> str:
    """The affiliate toggle is global, so each test gets its own database."""
    url = create_database()
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    with admin_client(database_url) as test_client:
        yield test_client


def execute(url: str, sql: str, *params: object) -> Any:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else None


def enable(url: str, on: bool = True) -> None:
    execute(
        url,
        "UPDATE app_settings SET value = %s WHERE key = 'affiliate_links_enabled'",
        Jsonb(on),
    )


@pytest.fixture
def clip(database_url: str) -> str:
    url = database_url
    [(video,)] = execute(
        url,
        "INSERT INTO videos (youtube_video_id, title, channel_id) "
        "VALUES (%s, 'Source video', 'UCx') RETURNING id",
        uuid.uuid4().hex[:11],
    )
    [(job,)] = execute(
        url,
        "INSERT INTO processing_jobs (video_id, idempotency_key, status, stage) "
        "VALUES (%s, %s, 'completed', 'completed') RETURNING id",
        video,
        uuid.uuid4().hex,
    )
    [(candidate,)] = execute(
        url,
        "INSERT INTO clip_candidates (job_id, start_ms, end_ms, hook_score, emotion_score, "
        "clarity_score, relevance_score, visual_score, completeness_score, total_score, title, "
        "hook_line) VALUES (%s, 0, 30000, 1, 1, 1, 1, 1, 1, 1, 'Clip title', 'Wait for it') "
        "RETURNING id",
        job,
    )
    [(clip_id,)] = execute(
        url,
        "INSERT INTO clips (candidate_id, job_id, video_id, start_ms, end_ms, title) "
        "VALUES (%s, %s, %s, 0, 30000, 'Clip title') RETURNING id",
        candidate,
        job,
        video,
    )
    return str(clip_id)


def product(url: str, name: str) -> str:
    [(product_id,)] = execute(
        url,
        "INSERT INTO affiliate_products (name, merchant, affiliate_url, destination_domain) "
        "VALUES (%s, 'Shop', %s, 'shop.example.com') RETURNING id",
        name,
        f"https://shop.example.com/{name.replace(' ', '-').lower()}?tag=t-20",
    )
    return str(product_id)


def suggest(url: str, clip: str, name: str, score: str = "0.9") -> str:
    [(link,)] = execute(
        url,
        "INSERT INTO clip_affiliate_links (clip_id, product_id, relevance_score, match_reason) "
        "VALUES (%s, %s, %s, 'Related by keywords: x') RETURNING id",
        clip,
        product(url, name),
        score,
    )
    return str(link)


def links_url(clip: str) -> str:
    return f"/api/v1/clips/{clip}/affiliate-links"


def review(client: TestClient, clip: str, link: str, **body: object) -> Any:
    return client.post(f"{links_url(clip)}/{link}/review", json=body)


def caption(client: TestClient, clip: str) -> dict[str, Any]:
    result: dict[str, Any] = client.get(f"/api/v1/clips/{clip}").json()["caption"]
    return result


def test_only_approved_links_are_exported_with_the_disclosure(
    client: TestClient, database_url: str, clip: str
) -> None:
    enable(database_url)
    shoes = suggest(database_url, clip, "Trail shoes")
    lamp = suggest(database_url, clip, "Desk lamp", "0.8")
    suggest(database_url, clip, "Only suggested", "0.75")

    approved = review(client, clip, shoes, decision="approved")
    rejected = review(client, clip, lamp, decision="rejected")

    assert approved.status_code == 200, approved.text
    assert rejected.status_code == 200, rejected.text
    draft = caption(client, clip)
    assert [link["name"] for link in draft["links"]] == ["Trail shoes"]
    assert "https://shop.example.com/trail-shoes?tag=t-20" in draft["text"]
    assert "desk-lamp" not in draft["text"]
    assert "only-suggested" not in draft["text"]
    assert draft["disclosure"] == DISCLOSURE
    assert DISCLOSURE in draft["text"]
    assert draft["text"].startswith("Clip title\n\nWait for it\n\nFrom: Source video")


def test_first_approval_becomes_primary_and_primary_can_move(
    client: TestClient, database_url: str, clip: str
) -> None:
    enable(database_url)
    first = suggest(database_url, clip, "First")
    second = suggest(database_url, clip, "Second")

    review(client, clip, first, decision="approved")
    review(client, clip, second, decision="approved")
    moved = review(client, clip, second, decision="approved", primary=True)

    assert moved.status_code == 200, moved.text
    items = {item["id"]: item for item in moved.json()["items"]}
    assert items[first]["is_primary"] is False
    assert items[second]["is_primary"] is True
    assert items[second]["reviewed_at"] is not None
    events = execute(
        database_url,
        "SELECT event_type FROM affiliate_events WHERE clip_id = %s ORDER BY id",
        clip,
    )
    assert [event for (event,) in events] == ["approved", "approved", "approved"]


def test_toggle_off_exports_no_urls_even_with_approved_rows(
    client: TestClient, database_url: str, clip: str
) -> None:
    enable(database_url)
    link = suggest(database_url, clip, "Trail shoes")
    review(client, clip, link, decision="approved")
    enable(database_url, False)

    draft = caption(client, clip)
    listing = client.get(links_url(clip)).json()

    assert draft["links"] == []
    assert "https://" not in draft["text"]
    assert draft["disclosure"] is None
    assert listing["enabled"] is False
    assert [item["status"] for item in listing["items"]] == ["approved"], "kept for audit"


def test_disabled_toggle_blocks_approval_and_manual_selection_but_not_removal(
    client: TestClient, database_url: str, clip: str
) -> None:
    link = suggest(database_url, clip, "Trail shoes")
    extra = product(database_url, "Manual pick")

    assert review(client, clip, link, decision="approved").status_code == 409
    manual = client.post(links_url(clip), json={"product_id": extra})
    assert manual.status_code == 409
    assert review(client, clip, link, decision="rejected").status_code == 200
    assert client.delete(f"{links_url(clip)}/{link}").status_code == 204
    assert client.get(links_url(clip)).json()["items"] == []
    events = execute(
        database_url,
        "SELECT event_type, affiliate_link_id, details->>'link_id' FROM affiliate_events "
        "WHERE clip_id = %s ORDER BY id",
        clip,
    )
    assert [(event, link_id) for event, link_id, _ in events] == [
        ("rejected", None),
        ("removed", None),
    ]
    assert {details_link for *_, details_link in events} == {link}


def test_manual_product_selection_creates_an_approved_link(
    client: TestClient, database_url: str, clip: str
) -> None:
    enable(database_url)
    pick = product(database_url, "Manual pick")

    response = client.post(links_url(clip), json={"product_id": pick, "primary": True})

    assert response.status_code == 201, response.text
    [item] = response.json()["items"]
    assert (item["product"]["id"], item["status"], item["is_primary"]) == (pick, "approved", True)
    assert "administrator" in item["match_reason"]
    again = client.post(links_url(clip), json={"product_id": pick})
    assert again.status_code == 409
    inactive = product(database_url, "Inactive")
    execute(database_url, "UPDATE affiliate_products SET active = false WHERE id = %s", inactive)
    assert client.post(links_url(clip), json={"product_id": inactive}).status_code == 422


def test_review_errors(client: TestClient, database_url: str, clip: str) -> None:
    enable(database_url)
    link = suggest(database_url, clip, "Trail shoes")

    assert review(client, clip, str(uuid.uuid4()), decision="approved").status_code == 404
    assert review(client, str(uuid.uuid4()), link, decision="approved").status_code == 404
    assert review(client, clip, link, decision="maybe").status_code == 422
    assert review(client, clip, link, decision="rejected", primary=True).status_code == 422


def test_caption_without_links_has_no_disclosure(client: TestClient, clip: str) -> None:
    draft = caption(client, clip)

    assert draft == {
        "text": "Clip title\n\nWait for it\n\nFrom: Source video",
        "links": [],
        "disclosure": None,
    }
