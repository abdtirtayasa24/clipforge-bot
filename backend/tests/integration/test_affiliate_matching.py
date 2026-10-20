import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from clipforge.affiliate.matcher import AffiliateMatcher, EmbeddingSetup
from clipforge.ai.registry import ProviderRegistry
from clipforge.ai.usage import ProviderCall, UsageRecorder
from clipforge.db.session import create_engine, create_session_factory
from clipforge.pipeline.stages.affiliate import AffiliateStage
from tests.api import admin_client
from tests.db import alembic_config, libpq_url
from tests.jobs import Sessions

TRANSCRIPT = "These trail running shoes survived fifty kilometers of mud"


@pytest.fixture
def database_url(create_database: Callable[[], str]) -> str:
    """The toggle and catalog are global, so each test gets its own database."""
    url = create_database()
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[Sessions]:
    engine = create_engine(database_url, pool_size=2)
    yield create_session_factory(engine)
    await engine.dispose()


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


def seed_clip(url: str, transcript: str = TRANSCRIPT) -> tuple[str, str]:
    [(video,)] = execute(
        url,
        "INSERT INTO videos (youtube_video_id, title, channel_id) VALUES (%s, 'v', 'UCx') "
        "RETURNING id",
        uuid.uuid4().hex[:11],
    )
    [(job,)] = execute(
        url,
        "INSERT INTO processing_jobs (video_id, idempotency_key, status, stage) "
        "VALUES (%s, %s, 'running', 'uploading') RETURNING id",
        video,
        uuid.uuid4().hex,
    )
    [(candidate,)] = execute(
        url,
        "INSERT INTO clip_candidates (job_id, start_ms, end_ms, hook_score, emotion_score, "
        "clarity_score, relevance_score, visual_score, completeness_score, total_score, title, "
        "transcript_excerpt, topic, keywords, language) VALUES (%s, 0, 30000, 1, 1, 1, 1, 1, 1, "
        "1, 'Trail run review', %s, 'trail running', %s, 'id') RETURNING id",
        job,
        transcript,
        ["running shoes"],
    )
    [(clip,)] = execute(
        url,
        "INSERT INTO clips (candidate_id, job_id, video_id, start_ms, end_ms, title) "
        "VALUES (%s, %s, %s, 0, 30000, 'Trail run review') RETURNING id",
        candidate,
        job,
        video,
    )
    return str(clip), str(job)


def seed_product(url: str, name: str, tags: list[str], *, active: bool = True) -> str:
    [(product,)] = execute(
        url,
        "INSERT INTO affiliate_products (name, merchant, affiliate_url, destination_domain, "
        "tags, active) VALUES (%s, 'Shop', 'https://shop.example.com/p?tag=t', "
        "'shop.example.com', %s, %s) RETURNING id",
        name,
        tags,
        active,
    )
    return str(product)


def links(url: str, clip: str) -> list[tuple[str, str, bool]]:
    rows = execute(
        url,
        "SELECT p.name, l.status, l.is_primary FROM clip_affiliate_links l "
        "JOIN affiliate_products p ON p.id = l.product_id WHERE l.clip_id = %s "
        "ORDER BY l.relevance_score DESC, p.name",
        clip,
    )
    return [tuple(row) for row in rows]


def context(job: str) -> Any:
    async def not_cancelled() -> None:
        return None

    lease = SimpleNamespace(job_id=uuid.UUID(job))
    snapshot = {"providers": {}}
    return SimpleNamespace(
        job=SimpleNamespace(lease=lease, settings_snapshot=snapshot),
        check_cancelled=not_cancelled,
    )


class FakeEmbedder:
    """Vectors over two axes, footwear and lighting, from words in the text."""

    def __init__(self, *, fail: bool = False) -> None:
        self.texts: list[str] = []
        self.fail = fail

    async def embed(self, texts: Sequence[str], *, record: UsageRecorder) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("quota exhausted")
        self.texts += texts
        await record(ProviderCall(success=True, latency_ms=1))
        return [
            [float("shoe" in text.lower()), float("lamp" in text.lower()) + 0.01] for text in texts
        ]


async def no_record(_: ProviderCall) -> None:
    return None


def setup(embedder: FakeEmbedder) -> EmbeddingSetup:
    return EmbeddingSetup(embedder, "fake-embedding", no_record)


async def test_toggle_off_makes_the_stage_a_no_op(sessions: Sessions, database_url: str) -> None:
    clip, job = seed_clip(database_url)
    seed_product(database_url, "Trail shoes", ["running shoes"])

    result = await AffiliateStage(sessions, ProviderRegistry()).run(context(job))

    assert result.details == {"enabled": False}
    assert links(database_url, clip) == []
    assert execute(database_url, "SELECT count(*) FROM affiliate_events") == [(0,)]


async def test_stage_suggests_relevant_active_products_by_keyword(
    sessions: Sessions, database_url: str
) -> None:
    enable(database_url)
    clip, job = seed_clip(database_url)
    seed_product(database_url, "Trail shoes", ["running shoes"])
    seed_product(database_url, "Old shoes", ["running shoes"], active=False)
    seed_product(database_url, "Desk lamp", ["lighting"])

    result = await AffiliateStage(sessions, ProviderRegistry()).run(context(job))

    assert result.details == {"enabled": True, "suggestions": 1}
    assert links(database_url, clip) == [("Trail shoes", "suggested", True)]
    events = execute(
        database_url,
        "SELECT event_type, details->>'method' FROM affiliate_events WHERE clip_id = %s "
        "ORDER BY id",
        clip,
    )
    assert events == [("suggested", "keyword"), ("matched", "keyword")]
    reason = execute(database_url, "SELECT match_reason FROM clip_affiliate_links")[0][0]
    assert "running shoes" in reason


async def test_no_match_stores_no_links(sessions: Sessions, database_url: str) -> None:
    enable(database_url)
    clip, job = seed_clip(database_url, transcript="A quiet talk about poetry")
    execute(database_url, "UPDATE clip_candidates SET keywords = '{}', topic = 'poetry'")
    execute(database_url, "UPDATE clips SET title = 'Poems'")
    seed_product(database_url, "Trail shoes", ["running shoes"])

    await AffiliateStage(sessions, ProviderRegistry()).run(context(job))

    assert links(database_url, clip) == []


async def test_embeddings_rank_and_are_cached_until_the_product_changes(
    sessions: Sessions, database_url: str
) -> None:
    enable(database_url)
    clip, _ = seed_clip(database_url)
    shoes = seed_product(database_url, "Trail shoes", ["running shoes"])
    embedder = FakeEmbedder()

    assert await AffiliateMatcher(sessions, setup(embedder)).match_clip(clip) == 1
    assert len(embedder.texts) == 2, "clip query plus one product document"
    assert links(database_url, clip) == [("Trail shoes", "suggested", True)]

    embedder.texts.clear()
    await AffiliateMatcher(sessions, setup(embedder)).match_clip(clip, replace_suggested=True)
    assert len(embedder.texts) == 1, "the product embedding came from the cache"

    execute(
        database_url,
        "UPDATE affiliate_products SET tags = '{running shoes,trail}' WHERE id = %s",
        shoes,
    )
    embedder.texts.clear()
    await AffiliateMatcher(sessions, setup(embedder)).match_clip(clip, replace_suggested=True)
    assert len(embedder.texts) == 2, "an edited product is embedded again"


async def test_embedding_failure_falls_back_to_keywords(
    sessions: Sessions, database_url: str
) -> None:
    enable(database_url)
    clip, _ = seed_clip(database_url)
    seed_product(database_url, "Trail shoes", ["running shoes"])

    added = await AffiliateMatcher(sessions, setup(FakeEmbedder(fail=True))).match_clip(clip)

    assert added == 1
    method = execute(
        database_url, "SELECT details->>'method' FROM affiliate_events WHERE event_type = 'matched'"
    )
    assert method == [("keyword",)]


@pytest.fixture
def client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Without the key the API falls back to keyword matching: no network in tests.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with admin_client(database_url) as test_client:
        yield test_client


def test_rematch_is_rejected_when_disabled(client: TestClient, database_url: str) -> None:
    clip, _ = seed_clip(database_url)
    seed_product(database_url, "Trail shoes", ["running shoes"])

    response = client.post(f"/api/v1/clips/{clip}/affiliate-links/rematch")

    assert response.status_code == 409
    assert links(database_url, clip) == []
    assert execute(database_url, "SELECT count(*) FROM affiliate_events") == [(0,)]


def test_rematch_replaces_suggestions_and_keeps_reviewed_links(
    client: TestClient, database_url: str
) -> None:
    enable(database_url)
    clip, _ = seed_clip(database_url)
    approved = seed_product(database_url, "Approved shoes", ["running shoes"])
    stale = seed_product(database_url, "Stale shoes", ["running shoes"])
    for product, status in ((approved, "approved"), (stale, "suggested")):
        execute(
            database_url,
            "INSERT INTO clip_affiliate_links (clip_id, product_id, relevance_score, status) "
            "VALUES (%s, %s, 0.9, %s)",
            clip,
            product,
            status,
        )
    execute(database_url, "UPDATE affiliate_products SET active = false WHERE id = %s", stale)
    seed_product(database_url, "New shoes", ["running shoes"])
    seed_product(database_url, "Desk lamp", ["lighting"])

    response = client.post(f"/api/v1/clips/{clip}/affiliate-links/rematch")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True
    assert [(i["product"]["name"], i["status"]) for i in body["items"]] == [
        ("Approved shoes", "approved"),
        ("New shoes", "suggested"),
    ]
    assert client.post(f"/api/v1/clips/{uuid.uuid4()}/affiliate-links/rematch").status_code == 404


def test_rematch_without_a_match_suggests_nothing(client: TestClient, database_url: str) -> None:
    enable(database_url)
    clip, _ = seed_clip(database_url)
    seed_product(database_url, "Desk lamp", ["lighting"])

    response = client.post(f"/api/v1/clips/{clip}/affiliate-links/rematch")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    caption = client.get(f"/api/v1/clips/{clip}").json()["caption"]
    assert caption["links"] == [] and "https://" not in caption["text"]
