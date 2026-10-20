import uuid
from collections.abc import Callable

import psycopg
import pytest
from alembic import command
from sqlalchemy import text

from clipforge.db.session import create_engine, create_session_factory
from tests.db import alembic_config, libpq_url

CORE_TABLES = {
    "admin_users",
    "youtube_sources",
    "videos",
    "video_sources",
    "media_assets",
    "processing_jobs",
    "clip_candidates",
    "clips",
    "clip_reviews",
    "ai_provider_configs",
    "provider_usage",
    "app_settings",
    "oauth_credentials",
    "app_setting_events",
    "affiliate_products",
    "clip_affiliate_links",
    "affiliate_events",
}


def public_tables(url: str) -> set[str]:
    with psycopg.connect(libpq_url(url)) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {row[0] for row in rows}


def test_upgrade_downgrade_upgrade_round_trip(create_database: Callable[[], str]) -> None:
    url = create_database()
    config = alembic_config(url)

    command.upgrade(config, "head")
    assert CORE_TABLES.issubset(public_tables(url))

    command.downgrade(config, "base")
    assert public_tables(url) == {"alembic_version"}

    command.upgrade(config, "head")
    assert CORE_TABLES.issubset(public_tables(url))


def insert_video(conn: psycopg.Connection, youtube_video_id: str) -> object:
    row = conn.execute(
        "INSERT INTO videos (youtube_video_id, title, channel_id) "
        "VALUES (%s, 'title', 'UC123') RETURNING id",
        (youtube_video_id,),
    ).fetchone()
    assert row is not None
    return row[0]


def test_youtube_video_id_is_unique(db_conn: psycopg.Connection) -> None:
    insert_video(db_conn, "dQw4w9WgXcQ")

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_video(db_conn, "dQw4w9WgXcQ")


def test_video_sources_primary_key_is_video_and_source(db_conn: psycopg.Connection) -> None:
    video_id = insert_video(db_conn, "vid00000001")
    source = db_conn.execute(
        "INSERT INTO youtube_sources (kind, name, region_code) "
        "VALUES ('trending', 'Trending ID', 'ID') RETURNING id"
    ).fetchone()
    assert source is not None
    link = "INSERT INTO video_sources (video_id, source_id) VALUES (%s, %s)"
    db_conn.execute(link, (video_id, source[0]))

    with pytest.raises(psycopg.errors.UniqueViolation):
        db_conn.execute(link, (video_id, source[0]))


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        ("INSERT INTO youtube_sources (kind, name) VALUES ('rss', 'x')", ()),
        ("INSERT INTO youtube_sources (kind, name) VALUES ('channel', 'no channel id')", ()),
        (
            "INSERT INTO videos (youtube_video_id, title, channel_id, rights_status) "
            "VALUES ('v2', 't', 'c', 'maybe')",
            (),
        ),
        ("INSERT INTO admin_users (telegram_user_id) VALUES (0)", ()),
    ],
)
def test_check_constraints_reject_invalid_values(
    db_conn: psycopg.Connection, sql: str, params: tuple[object, ...]
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(sql, params)


def test_updated_at_is_maintained_by_trigger(db_conn: psycopg.Connection) -> None:
    video_id = insert_video(db_conn, "vid00000002")
    db_conn.execute(
        "UPDATE videos SET created_at = now() - interval '1 day', "
        "updated_at = now() - interval '1 day' WHERE id = %s",
        (video_id,),
    )

    db_conn.execute("UPDATE videos SET title = 'renamed' WHERE id = %s", (video_id,))

    row = db_conn.execute(
        "SELECT updated_at > created_at FROM videos WHERE id = %s", (video_id,)
    ).fetchone()
    assert row == (True,)


async def test_async_session_factory_connects(migrated_database_url: str) -> None:
    engine = create_engine(migrated_database_url, pool_size=1)
    sessions = create_session_factory(engine)

    async with sessions() as session:
        result = await session.execute(text("SELECT count(*) FROM videos"))

    # Other tests share this database, so only the query's success is meaningful.
    assert result.scalar_one() >= 0
    await engine.dispose()


@pytest.mark.parametrize("disabled", [True, False])
async def test_prepared_statements_toggle(migrated_database_url: str, disabled: bool) -> None:
    engine = create_engine(migrated_database_url, disable_prepared_statements=disabled)

    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        threshold = raw.driver_connection.prepare_threshold  # type: ignore[union-attr]

    assert (threshold is None) is disabled
    await engine.dispose()


def affiliate_clip(conn: psycopg.Connection) -> tuple[object, object, object]:
    """A clip plus two catalog products, for affiliate-link constraint tests."""
    video_id = insert_video(conn, f"aff{uuid.uuid4().hex[:8]}")
    job = conn.execute(
        "INSERT INTO processing_jobs (video_id, idempotency_key, status, stage) "
        "VALUES (%s, %s, 'completed', 'completed') RETURNING id",
        (video_id, uuid.uuid4().hex),
    ).fetchone()
    assert job is not None
    candidate = conn.execute(
        "INSERT INTO clip_candidates (job_id, start_ms, end_ms, hook_score, emotion_score, "
        "clarity_score, relevance_score, visual_score, completeness_score, total_score) "
        "VALUES (%s, 0, 30000, 1, 1, 1, 1, 1, 1, 1) RETURNING id",
        (job[0],),
    ).fetchone()
    assert candidate is not None
    clip = conn.execute(
        "INSERT INTO clips (candidate_id, job_id, video_id, start_ms, end_ms) "
        "VALUES (%s, %s, %s, 0, 30000) RETURNING id",
        (candidate[0], job[0], video_id),
    ).fetchone()
    assert clip is not None
    products = [
        conn.execute(
            "INSERT INTO affiliate_products (name, merchant, affiliate_url, destination_domain) "
            "VALUES (%s, 'm', 'https://shop.example.com/p', 'shop.example.com') RETURNING id",
            (name,),
        ).fetchone()
        for name in ("a", "b")
    ]
    assert products[0] is not None and products[1] is not None
    return clip[0], products[0][0], products[1][0]


def link(
    conn: psycopg.Connection, clip: object, product: object, status: str, primary: bool
) -> None:
    conn.execute(
        "INSERT INTO clip_affiliate_links (clip_id, product_id, relevance_score, status, "
        "is_primary) VALUES (%s, %s, 0.9, %s, %s)",
        (clip, product, status, primary),
    )


def test_one_approved_primary_affiliate_link_per_clip(db_conn: psycopg.Connection) -> None:
    clip, first, second = affiliate_clip(db_conn)
    link(db_conn, clip, first, "approved", True)

    with pytest.raises(psycopg.errors.UniqueViolation):
        link(db_conn, clip, second, "approved", True)


def test_suggested_primaries_do_not_count_toward_the_primary_limit(
    db_conn: psycopg.Connection,
) -> None:
    clip, first, second = affiliate_clip(db_conn)
    link(db_conn, clip, first, "approved", True)
    link(db_conn, clip, second, "suggested", True)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO clip_affiliate_links (clip_id, product_id, relevance_score) "
        "VALUES (%(clip)s, %(product)s, 1.5)",
        "INSERT INTO clip_affiliate_links (clip_id, product_id, relevance_score, status) "
        "VALUES (%(clip)s, %(product)s, 0.5, 'maybe')",
    ],
)
def test_affiliate_link_checks(db_conn: psycopg.Connection, statement: str) -> None:
    clip, product, _ = affiliate_clip(db_conn)

    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(statement, {"clip": clip, "product": product})


def test_affiliate_events_are_append_only(db_conn: psycopg.Connection) -> None:
    clip, _, _ = affiliate_clip(db_conn)
    db_conn.execute(
        "INSERT INTO affiliate_events (clip_id, event_type) VALUES (%s, 'suggested')", (clip,)
    )

    db_conn.execute(
        "UPDATE affiliate_events SET affiliate_link_id = NULL WHERE clip_id = %s", (clip,)
    )
    for statement in (
        "DELETE FROM affiliate_events WHERE clip_id = %s",
        "UPDATE affiliate_events SET event_type = 'x' WHERE clip_id = %s",
    ):
        db_conn.execute("SAVEPOINT probe")
        with pytest.raises(psycopg.errors.RaiseException):
            db_conn.execute(statement, (clip,))
        db_conn.execute("ROLLBACK TO SAVEPOINT probe")


def test_affiliate_product_updated_at_trigger(db_conn: psycopg.Connection) -> None:
    _, product, _ = affiliate_clip(db_conn)
    db_conn.execute(
        "UPDATE affiliate_products SET updated_at = now() - interval '1 day' WHERE id = %s",
        (product,),
    )

    db_conn.execute("UPDATE affiliate_products SET name = 'renamed' WHERE id = %s", (product,))

    row = db_conn.execute(
        "SELECT updated_at > now() - interval '1 minute' FROM affiliate_products WHERE id = %s",
        (product,),
    ).fetchone()
    assert row == (True,)
