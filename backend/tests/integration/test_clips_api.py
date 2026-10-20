import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.api import admin_client
from tests.db import libpq_url

CLIPS = "/api/v1/clips"


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    with admin_client(migrated_database_url) as test_client:
        yield test_client


def execute(url: str, sql: str, *params: object) -> Any:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if cursor.description else None


def new_video(url: str, title: str = "Source video") -> str:
    row = execute(
        url,
        "INSERT INTO videos (youtube_video_id, title, channel_id) VALUES (%s, %s, 'UCx') "
        "RETURNING id",
        uuid.uuid4().hex[:11],
        title,
    )
    return str(row[0])


def new_job(url: str, video_id: str) -> str:
    row = execute(
        url,
        "INSERT INTO processing_jobs (video_id, idempotency_key, status, stage) "
        "VALUES (%s, %s, 'completed', 'completed') RETURNING id",
        video_id,
        uuid.uuid4().hex,
    )
    return str(row[0])


def new_clip(
    url: str,
    job_id: str,
    video_id: str,
    *,
    title: str = "Generated title",
    rank: int = 1,
    created_at: str | None = None,
) -> str:
    candidate = execute(
        url,
        "INSERT INTO clip_candidates (job_id, start_ms, end_ms, hook_score, emotion_score, "
        "clarity_score, relevance_score, visual_score, completeness_score, total_score, title, "
        "hook_line, explanation, transcript_excerpt, selected, rank) "
        "VALUES (%s, 1000, 31000, 90, 80, 70, 60, 50, 40, 81.5, %s, 'Wait for it', "
        "'Strong opening hook', 'So here is the thing', true, %s) RETURNING id",
        job_id,
        title,
        rank,
    )
    clip = execute(
        url,
        "INSERT INTO clips (candidate_id, job_id, video_id, start_ms, end_ms, title, "
        "drive_file_id, drive_web_view_link, created_at) "
        "VALUES (%s, %s, %s, 1000, 31000, %s, 'drive-1', 'https://drive.google.com/file/d/x', "
        "coalesce(%s::timestamptz, now())) RETURNING id",
        candidate[0],
        job_id,
        video_id,
        title,
        created_at,
    )
    return str(clip[0])


@pytest.fixture
def clip(migrated_database_url: str) -> dict[str, str]:
    video_id = new_video(migrated_database_url)
    job_id = new_job(migrated_database_url, video_id)
    clip_id = new_clip(migrated_database_url, job_id, video_id)
    return {"id": clip_id, "job_id": job_id, "video_id": video_id}


def review(client: TestClient, clip_id: str, decision: str, **body: object) -> dict[str, Any]:
    response = client.post(f"{CLIPS}/{clip_id}/review", json={"decision": decision, **body})
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_clip_detail_has_scores_transcript_drive_artifacts_and_cost(
    client: TestClient, migrated_database_url: str, clip: dict[str, str]
) -> None:
    execute(
        migrated_database_url,
        "INSERT INTO clip_artifacts (clip_id, kind, size_bytes, upload_state, drive_file_id) "
        "VALUES (%s, 'video', 1234, 'verified', 'drive-1')",
        clip["id"],
    )
    execute(
        migrated_database_url,
        "INSERT INTO provider_usage (job_id, provider, model, capability, success, latency_ms, "
        "estimated_cost) VALUES (%s, 'gemini', 'm', 'analysis', true, 10, %s)",
        clip["job_id"],
        Decimal("0.0125"),
    )

    response = client.get(f"{CLIPS}/{clip['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Generated title"
    assert body["title_override"] is None
    assert body["effective_title"] == "Generated title"
    assert body["video_title"] == "Source video"
    assert body["hook_line"] == "Wait for it"
    assert body["explanation"] == "Strong opening hook"
    assert body["transcript_excerpt"] == "So here is the thing"
    assert body["scores"] == {
        "hook": 90,
        "emotion": 80,
        "clarity": 70,
        "relevance": 60,
        "visual": 50,
        "completeness": 40,
        "total": 81.5,
    }
    assert body["drive_web_view_link"] == "https://drive.google.com/file/d/x"
    assert [(a["kind"], a["upload_state"], a["size_bytes"]) for a in body["artifacts"]] == [
        ("video", "verified", 1234)
    ]
    assert "local_path" not in body["artifacts"][0]
    assert body["job_costs"] == [{"currency": "USD", "estimated": "0.0125"}]
    assert body["reviews"] == []
    assert body["review_status"] == "pending_review"


def test_unknown_clip_is_404(client: TestClient) -> None:
    assert client.get(f"{CLIPS}/{uuid.uuid4()}").status_code == 404
    response = client.post(f"{CLIPS}/{uuid.uuid4()}/review", json={"decision": "approved"})
    assert response.status_code == 404


def test_list_filters_by_status_video_and_date(
    client: TestClient, migrated_database_url: str
) -> None:
    url = migrated_database_url
    video_id = new_video(url)
    job_id = new_job(url, video_id)
    old = new_clip(url, job_id, video_id, rank=1, created_at="2026-01-05T10:00:00Z")
    recent = new_clip(url, job_id, video_id, rank=2, created_at="2026-03-05T10:00:00Z")
    review(client, recent, "approved")

    by_video = client.get(CLIPS, params={"video_id": video_id}).json()
    assert [item["id"] for item in by_video["items"]] == [recent, old]
    assert by_video["total"] == 2

    approved = client.get(CLIPS, params={"video_id": video_id, "status": "approved"}).json()
    assert [item["id"] for item in approved["items"]] == [recent]
    assert approved["items"][0]["effective_title"] == "Generated title"

    ranged = client.get(
        CLIPS,
        params={
            "video_id": video_id,
            "created_from": "2026-01-01T00:00:00Z",
            "created_to": "2026-02-01T00:00:00Z",
        },
    ).json()
    assert [item["id"] for item in ranged["items"]] == [old]

    assert client.get(CLIPS, params={"status": "bogus"}).status_code == 422


def test_review_is_append_only_and_re_review_is_recorded(
    client: TestClient, migrated_database_url: str, clip: dict[str, str]
) -> None:
    first = review(client, clip["id"], "approved", note="good hook")
    assert first["review_status"] == "approved"

    second = review(client, clip["id"], "rejected", note="changed my mind")

    assert second["review_status"] == "rejected"
    assert [(r["decision"], r["note"]) for r in second["reviews"]] == [
        ("approved", "good hook"),
        ("rejected", "changed my mind"),
    ]
    assert all(r["admin_user_id"] for r in second["reviews"])
    count = execute(
        migrated_database_url,
        "SELECT count(*) FROM clip_reviews WHERE clip_id = %s",
        clip["id"],
    )
    assert count[0] == 2


def test_title_override_is_audited_and_never_replaces_the_generated_title(
    client: TestClient, migrated_database_url: str, clip: dict[str, str]
) -> None:
    edited = review(client, clip["id"], "approved", title_override="  My better title  ")

    assert edited["title"] == "Generated title"
    assert edited["title_override"] == "My better title"
    assert edited["effective_title"] == "My better title"
    assert edited["reviews"][-1]["title_override"] == "My better title"

    kept = review(client, clip["id"], "approved", note="no title change")
    assert kept["effective_title"] == "My better title"
    assert kept["reviews"][-1]["title_override"] is None

    row = execute(
        migrated_database_url, "SELECT title, title_override FROM clips WHERE id = %s", clip["id"]
    )
    assert row == ("Generated title", "My better title")
    listed = client.get(CLIPS, params={"video_id": clip["video_id"]}).json()
    assert listed["items"][0]["effective_title"] == "My better title"


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "maybe"},
        {"decision": "approved", "title_override": "   "},
        {"decision": "approved", "title_override": "x" * 101},
        {"decision": "approved", "note": "n" * 2001},
        {"decision": "approved", "extra": 1},
    ],
)
def test_invalid_review_is_rejected(
    client: TestClient, clip: dict[str, str], body: dict[str, object]
) -> None:
    response = client.post(f"{CLIPS}/{clip['id']}/review", json=body)
    assert response.status_code == 422


def test_clip_routes_require_admin(migrated_database_url: str, clip: dict[str, str]) -> None:
    with admin_client(migrated_database_url) as anonymous:
        anonymous.headers.pop("Authorization")
        assert anonymous.get(CLIPS).status_code == 401
        assert anonymous.get(f"{CLIPS}/{clip['id']}").status_code == 401
        response = anonymous.post(f"{CLIPS}/{clip['id']}/review", json={"decision": "approved"})
        assert response.status_code == 401
