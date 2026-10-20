import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import event

from tests.api import admin_client
from tests.db import alembic_config, libpq_url

DASHBOARD = "/api/v1/dashboard"


@pytest.fixture
def database_url(create_database: Callable[[], str]) -> str:
    """Dashboard totals cover the whole database, so each test gets its own."""
    url = create_database()
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def client(database_url: str, tmp_path: Any) -> Iterator[TestClient]:
    with admin_client(database_url, workspace_dir=tmp_path) as test_client:
        yield test_client


def execute(url: str, sql: str, *params: object) -> Any:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if cursor.description else None


def new_job(url: str, status: str = "completed", finished_days_ago: int | None = 1) -> str:
    video = execute(
        url,
        "INSERT INTO videos (youtube_video_id, title, channel_id) VALUES (%s, 'v', 'UCx') "
        "RETURNING id",
        uuid.uuid4().hex[:11],
    )
    job = execute(
        url,
        "INSERT INTO processing_jobs (video_id, idempotency_key, status, stage, finished_at) "
        "VALUES (%s, %s, %s, %s, now() - make_interval(days => %s)) RETURNING id",
        video[0],
        uuid.uuid4().hex,
        status,
        status if status != "running" else "transcribed",
        finished_days_ago,
    )
    return str(job[0])


def usage(
    url: str,
    job_id: str | None,
    cost: str,
    *,
    days_ago: int,
    model: str = "gemini-3.5-flash-lite",
    stage: str = "analyzed",
    currency: str = "USD",
) -> Decimal:
    execute(
        url,
        "INSERT INTO provider_usage (job_id, provider, model, capability, success, latency_ms, "
        "stage, estimated_cost, currency, created_at) "
        "VALUES (%s, 'gemini', %s, 'analysis', true, 10, %s, %s, %s, "
        "now() - make_interval(days => %s))",
        job_id,
        model,
        stage,
        Decimal(cost),
        currency,
        days_ago,
    )
    return Decimal(cost)


def money(lines: list[dict[str, str]], **match: str) -> dict[str, Decimal]:
    """Currency -> amount for the lines whose other fields equal `match`."""
    return {
        line["currency"]: Decimal(line["estimated"])
        for line in lines
        if all(line.get(key) == value for key, value in match.items())
    }


def test_cost_aggregates_match_the_usage_rows(client: TestClient, database_url: str) -> None:
    url = database_url
    job_a = new_job(url)
    job_b = new_job(url)
    failed = new_job(url, status="failed")
    a1 = usage(url, job_a, "0.010", days_ago=1, stage="transcribed", model="gemini-3.5-transcribe")
    a2 = usage(url, job_a, "0.020", days_ago=1)
    b1 = usage(url, job_b, "0.030", days_ago=10)
    f1 = usage(url, failed, "0.005", days_ago=2)
    eur = usage(url, None, "1.50", days_ago=3, currency="EUR")
    usage(url, None, "9.99", days_ago=45)

    response = client.get(DASHBOARD)

    assert response.status_code == 200, response.text
    costs = response.json()["costs"]
    week, month = costs["last_7_days"], costs["last_30_days"]
    assert money(week["totals"]) == {"USD": a1 + a2 + f1, "EUR": eur}
    assert money(month["totals"]) == {"USD": a1 + a2 + b1 + f1, "EUR": eur}
    assert money(month["by_stage"], stage="transcribed") == {"USD": a1}
    assert money(month["by_stage"], stage="analyzed") == {"USD": a2 + b1 + f1, "EUR": eur}
    assert money(month["by_model"], provider="gemini", model="gemini-3.5-transcribe") == {"USD": a1}
    assert money(month["by_model"], model="gemini-3.5-flash-lite") == {
        "USD": a2 + b1 + f1,
        "EUR": eur,
    }
    assert sum(money(month["by_day"]).values(), Decimal(0)) > 0
    assert sum(Decimal(line["estimated"]) for line in month["by_day"]) == a1 + a2 + b1 + f1 + eur
    assert len({line["day"] for line in month["by_day"]}) == 4
    assert money(costs["average_per_completed_job"]) == {"USD": (a1 + a2 + b1) / 2}
    assert costs["completed_jobs"] == 2


def test_dashboard_counts_jobs_reviews_discovery_and_disk(
    client: TestClient, database_url: str
) -> None:
    url = database_url
    job = new_job(url)
    new_job(url, status="failed")
    new_job(url, status="running", finished_days_ago=None)
    video = execute(url, "SELECT video_id FROM processing_jobs WHERE id = %s", job)[0]
    for rank, review_status in enumerate(["pending_review", "pending_review", "approved"], 1):
        candidate = execute(
            url,
            "INSERT INTO clip_candidates (job_id, start_ms, end_ms, hook_score, emotion_score, "
            "clarity_score, relevance_score, visual_score, completeness_score, total_score, "
            "rank) VALUES (%s, 0, 30000, 1, 1, 1, 1, 1, 1, 1, %s) RETURNING id",
            job,
            rank,
        )
        execute(
            url,
            "INSERT INTO clips (candidate_id, job_id, video_id, start_ms, end_ms, review_status) "
            "VALUES (%s, %s, %s, 0, 30000, %s)",
            candidate[0],
            job,
            video,
            review_status,
        )
    execute(
        url,
        "INSERT INTO youtube_sources (kind, name, region_code, last_run_at) "
        "VALUES ('trending', 'Trending', 'ID', '2026-10-20T01:00:00Z')",
    )

    body = client.get(DASHBOARD).json()

    assert body["jobs_by_status"] == {"completed": 1, "failed": 1, "running": 1}
    assert body["clips_awaiting_review"] == 2
    assert datetime.fromisoformat(body["last_discovery_run_at"]) == datetime(
        2026, 10, 20, 1, tzinfo=UTC
    )
    assert body["disk_free_bytes"] > 0
    assert body["costs"]["last_7_days"]["totals"] == []
    assert body["costs"]["average_per_completed_job"] == []


def count_statements(client: TestClient) -> int:
    engine = client.app.state.engine.sync_engine  # type: ignore[attr-defined]
    statements: list[str] = []

    def record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get(DASHBOARD).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(statements)


def test_dashboard_uses_a_bounded_number_of_statements(
    client: TestClient, database_url: str
) -> None:
    baseline = count_statements(client)
    for _ in range(5):
        job = new_job(database_url)
        for days_ago in range(0, 30, 3):
            usage(database_url, job, "0.001", days_ago=days_ago, stage=f"s{days_ago}")

    assert count_statements(client) == baseline
    assert baseline <= 8


def test_dashboard_requires_admin(database_url: str) -> None:
    with admin_client(database_url) as anonymous:
        anonymous.headers.pop("Authorization")
        assert anonymous.get(DASHBOARD).status_code == 401
