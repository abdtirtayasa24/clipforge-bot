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

    assert result.scalar_one() == 0
    await engine.dispose()


@pytest.mark.parametrize("disabled", [True, False])
async def test_prepared_statements_toggle(migrated_database_url: str, disabled: bool) -> None:
    engine = create_engine(migrated_database_url, disable_prepared_statements=disabled)

    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        threshold = raw.driver_connection.prepare_threshold  # type: ignore[union-attr]

    assert (threshold is None) is disabled
    await engine.dispose()
