import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Response
from psycopg.types.json import Jsonb

from tests.api import admin_client
from tests.db import libpq_url

JOBS = "/api/v1/jobs"
EVIDENCE = {"type": "own_content", "statement": "I am the creator of this video."}


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    with admin_client(migrated_database_url) as test_client:
        yield test_client


def execute(url: str, sql: str, *params: object) -> Any:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if cursor.description else None


def new_video(url: str) -> str:
    row = execute(
        url,
        "INSERT INTO videos (youtube_video_id, title, channel_id) VALUES (%s, 'v', 'UCx') "
        "RETURNING id",
        uuid.uuid4().hex[:11],
    )
    return str(row[0])


def add_ready_upload(url: str, video_id: str) -> str:
    row = execute(
        url,
        "INSERT INTO media_assets (video_id, kind, permission_evidence, state) "
        "VALUES (%s, 'upload', %s, 'ready') RETURNING id",
        video_id,
        Jsonb(EVIDENCE),
    )
    return str(row[0])


def queue_job(client: TestClient, video_id: str, key: str | None = None) -> Response:
    headers = {"Idempotency-Key": key or uuid.uuid4().hex}
    return client.post(f"/api/v1/videos/{video_id}/jobs", headers=headers)


def set_status(url: str, job_id: str, status: str) -> None:
    lease = status == "running"
    execute(
        url,
        "UPDATE processing_jobs SET status = %s, lease_token = %s, "
        "lease_expires_at = CASE WHEN %s THEN now() + interval '5 minutes' END WHERE id = %s",
        status,
        uuid.uuid4() if lease else None,
        lease,
        job_id,
    )


@pytest.fixture
def ready_video(migrated_database_url: str) -> tuple[str, str]:
    video_id = new_video(migrated_database_url)
    return video_id, add_ready_upload(migrated_database_url, video_id)


def test_queue_job_is_idempotent_per_key(client: TestClient, ready_video: tuple[str, str]) -> None:
    video_id, asset_id = ready_video

    first = queue_job(client, video_id, "key-a-" + video_id)
    again = queue_job(client, video_id, "key-a-" + video_id)

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    body = first.json()
    assert (body["status"], body["stage"], body["media_asset_id"]) == ("queued", "queued", asset_id)


def test_new_key_while_a_job_is_active_conflicts(
    client: TestClient, ready_video: tuple[str, str]
) -> None:
    video_id, _ = ready_video
    assert queue_job(client, video_id).status_code == 201

    response = queue_job(client, video_id)

    assert response.status_code == 409


def test_key_reused_for_another_video_conflicts(
    client: TestClient, migrated_database_url: str, ready_video: tuple[str, str]
) -> None:
    key = uuid.uuid4().hex
    assert queue_job(client, ready_video[0], key).status_code == 201

    response = queue_job(client, new_video(migrated_database_url), key)

    assert response.status_code == 409


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": ""}, {"Idempotency-Key": "k" * 201}])
def test_missing_or_invalid_idempotency_key_is_a_bad_request(
    client: TestClient, ready_video: tuple[str, str], headers: dict[str, str]
) -> None:
    response = client.post(f"/api/v1/videos/{ready_video[0]}/jobs", headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_unknown_video_is_not_found(client: TestClient) -> None:
    assert queue_job(client, str(uuid.uuid4())).status_code == 404


def test_job_without_source_awaits_it_until_a_url_is_registered(
    client: TestClient, migrated_database_url: str
) -> None:
    video_id = new_video(migrated_database_url)
    execute(
        migrated_database_url,
        "UPDATE app_settings SET value = %s WHERE key = 'permitted_download_domains'",
        Jsonb(["media.example.com"]),
    )
    try:
        job = queue_job(client, video_id).json()
        assert (job["status"], job["media_asset_id"]) == ("awaiting_source", None)

        media = client.post(
            f"/api/v1/videos/{video_id}/media",
            json={"url": "https://media.example.com/v.mp4", "permission": EVIDENCE},
        ).json()
    finally:
        execute(
            migrated_database_url,
            "UPDATE app_settings SET value = '[]' WHERE key = 'permitted_download_domains'",
        )

    detail = client.get(f"{JOBS}/{job['id']}").json()
    assert (detail["status"], detail["stage"]) == ("queued", "queued")
    assert detail["media_asset_id"] == media["id"]
    assert [event["event_type"] for event in detail["events"]] == ["created", "source_attached"]


def test_list_jobs_filters_and_paginates(
    client: TestClient, migrated_database_url: str, ready_video: tuple[str, str]
) -> None:
    video_id, _ = ready_video
    first = queue_job(client, video_id).json()
    set_status(migrated_database_url, first["id"], "completed")
    second = queue_job(client, video_id).json()

    response = client.get(JOBS, params={"video_id": video_id})
    queued = client.get(JOBS, params={"video_id": video_id, "status": "queued"})
    page = client.get(JOBS, params={"video_id": video_id, "limit": 1, "offset": 1})

    assert [job["id"] for job in response.json()["items"]] == [second["id"], first["id"]]
    assert response.json()["total"] == 2
    assert [job["id"] for job in queued.json()["items"]] == [second["id"]]
    assert [job["id"] for job in page.json()["items"]] == [first["id"]]
    assert client.get(JOBS, params={"status": "sleeping"}).status_code == 422


def test_job_detail_has_events_and_estimated_cost(
    client: TestClient, migrated_database_url: str, ready_video: tuple[str, str]
) -> None:
    job = queue_job(client, ready_video[0]).json()
    for cost in ("0.00120000", "0.00030000"):
        execute(
            migrated_database_url,
            "INSERT INTO provider_usage (job_id, provider, model, capability, success, "
            "latency_ms, estimated_cost) VALUES (%s, 'gemini', 'm', 'analysis', true, 10, %s)",
            job["id"],
            Decimal(cost),
        )

    detail = client.get(f"{JOBS}/{job['id']}").json()

    assert detail["attempts"] == 0
    assert detail["events"][0]["event_type"] == "created"
    assert detail["costs"] == [{"currency": "USD", "estimated": "0.0015"}]


def test_job_detail_breaks_estimated_cost_down_by_stage(
    client: TestClient, migrated_database_url: str, ready_video: tuple[str, str]
) -> None:
    job = queue_job(client, ready_video[0]).json()
    for stage, cost in (("transcribe", "0.002"), ("analyze", "0.0005"), ("analyze", "0.0001")):
        execute(
            migrated_database_url,
            "INSERT INTO provider_usage (job_id, provider, model, capability, stage, success, "
            "latency_ms, estimated_cost) VALUES (%s, 'gemini', 'm', 'analysis', %s, true, 10, %s)",
            job["id"],
            stage,
            Decimal(cost),
        )

    detail = client.get(f"{JOBS}/{job['id']}").json()

    assert detail["costs_by_stage"] == [
        {"stage": "analyze", "currency": "USD", "estimated": "0.0006"},
        {"stage": "transcribe", "currency": "USD", "estimated": "0.002"},
    ]


def test_unknown_job_is_not_found(client: TestClient) -> None:
    unknown = f"{JOBS}/{uuid.uuid4()}"

    assert client.get(unknown).status_code == 404
    assert client.post(f"{unknown}/retry").status_code == 404
    assert client.post(f"{unknown}/cancel").status_code == 404


def test_retry_requeues_a_failed_job_with_fresh_attempts(
    client: TestClient, migrated_database_url: str, ready_video: tuple[str, str]
) -> None:
    job = queue_job(client, ready_video[0]).json()
    execute(
        migrated_database_url,
        "UPDATE processing_jobs SET status = 'failed', stage = 'ingesting', attempts = 3, "
        "last_error = 'boom', finished_at = now() WHERE id = %s",
        job["id"],
    )

    response = client.post(f"{JOBS}/{job['id']}/retry")

    body = response.json()
    assert response.status_code == 200
    assert (body["status"], body["stage"], body["attempts"]) == ("queued", "ingesting", 0)
    assert (body["last_error"], body["finished_at"]) == (None, None)
    events = client.get(f"{JOBS}/{job['id']}").json()["events"]
    assert events[-1]["event_type"] == "retried"


@pytest.mark.parametrize("status", ["queued", "running", "completed", "cancelled"])
def test_retry_of_a_job_that_did_not_fail_conflicts(
    client: TestClient, migrated_database_url: str, ready_video: tuple[str, str], status: str
) -> None:
    job = queue_job(client, ready_video[0]).json()
    set_status(migrated_database_url, job["id"], status)

    assert client.post(f"{JOBS}/{job['id']}/retry").status_code == 409


def test_retry_conflicts_while_another_job_of_the_video_is_active(
    client: TestClient, migrated_database_url: str, ready_video: tuple[str, str]
) -> None:
    failed = queue_job(client, ready_video[0]).json()
    set_status(migrated_database_url, failed["id"], "failed")
    assert queue_job(client, ready_video[0]).status_code == 201

    assert client.post(f"{JOBS}/{failed['id']}/retry").status_code == 409


def test_cancel_of_a_waiting_job_cancels_it(
    client: TestClient, ready_video: tuple[str, str]
) -> None:
    job = queue_job(client, ready_video[0]).json()

    body = client.post(f"{JOBS}/{job['id']}/cancel").json()

    assert (body["status"], body["cancel_requested"]) == ("cancelled", True)
    assert body["finished_at"] is not None


def test_cancel_of_a_running_job_asks_the_worker_to_stop(
    client: TestClient, migrated_database_url: str, ready_video: tuple[str, str]
) -> None:
    job = queue_job(client, ready_video[0]).json()
    set_status(migrated_database_url, job["id"], "running")

    response = client.post(f"{JOBS}/{job['id']}/cancel")

    body = response.json()
    assert response.status_code == 200
    assert (body["status"], body["cancel_requested"]) == ("running", True)
    events = client.get(f"{JOBS}/{job['id']}").json()["events"]
    assert events[-1]["event_type"] == "cancel_requested"


@pytest.mark.parametrize("status", ["completed", "completed_no_clips", "failed", "cancelled"])
def test_cancel_of_a_finished_job_conflicts(
    client: TestClient, migrated_database_url: str, ready_video: tuple[str, str], status: str
) -> None:
    job = queue_job(client, ready_video[0]).json()
    set_status(migrated_database_url, job["id"], status)

    assert client.post(f"{JOBS}/{job['id']}/cancel").status_code == 409
