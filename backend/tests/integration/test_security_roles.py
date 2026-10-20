import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from tests.db import libpq_url

APP_ROLE = "clipforge_app"
APP_LOGIN_ROLE = "clipforge_app_login_test"


@pytest.fixture(scope="module")
def app_login_role(migrated_database_url: str) -> str:
    """Mirrors production: a login user that is a member of the NOLOGIN clipforge_app role."""
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_LOGIN_ROLE,)
        ).fetchone()
        if not exists:
            conn.execute(f"CREATE ROLE {APP_LOGIN_ROLE} LOGIN IN ROLE {APP_ROLE}")
    return APP_LOGIN_ROLE


@pytest.fixture
def as_app(db_conn: psycopg.Connection, app_login_role: str) -> Iterator[psycopg.Connection]:
    db_conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(app_login_role)))
    yield db_conn


def app_tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename <> 'alembic_version' ORDER BY tablename"
    ).fetchall()
    return [row[0] for row in rows]


def test_app_role_is_nologin_group(db_conn: psycopg.Connection) -> None:
    row = db_conn.execute(
        "SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
        "FROM pg_roles WHERE rolname = %s",
        (APP_ROLE,),
    ).fetchone()

    assert row == (False, False, False, False, False)


def test_app_role_can_select_insert_update(as_app: psycopg.Connection) -> None:
    row = as_app.execute(
        "INSERT INTO videos (youtube_video_id, title, channel_id) "
        "VALUES ('sec00000001', 't', 'c') RETURNING id"
    ).fetchone()
    assert row is not None
    as_app.execute("UPDATE videos SET title = 'u' WHERE id = %s", (row[0],))
    job = as_app.execute(
        "INSERT INTO processing_jobs (video_id, idempotency_key) VALUES (%s, %s) RETURNING id",
        (row[0], uuid.uuid4().hex),
    ).fetchone()
    assert job is not None
    as_app.execute("INSERT INTO job_events (job_id, event_type) VALUES (%s, 'created')", (job[0],))

    selected = as_app.execute("SELECT title FROM videos WHERE id = %s", (row[0],)).fetchone()

    assert selected == ("u",)


def test_app_role_can_read_every_app_table(as_app: psycopg.Connection) -> None:
    for table in app_tables(as_app):
        as_app.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE intruder (id int)",
        "ALTER TABLE videos ADD COLUMN intruder int",
        "DROP TABLE videos",
        "TRUNCATE videos CASCADE",
        "SELECT * FROM alembic_version",
        "DELETE FROM clip_reviews",
        "UPDATE clip_reviews SET note = 'x'",
        "DELETE FROM app_setting_events",
        "UPDATE app_setting_events SET setting_key = 'x'",
    ],
)
def test_app_role_has_no_ddl_or_privileged_access(
    as_app: psycopg.Connection, statement: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_app.execute(statement)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_supabase_api_roles_cannot_read_any_table(db_conn: psycopg.Connection, role: str) -> None:
    tables = [*app_tables(db_conn), "alembic_version"]
    db_conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))

    for table in tables:
        db_conn.execute("SAVEPOINT probe")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db_conn.execute(sql.SQL("SELECT 1 FROM {}").format(sql.Identifier(table)))
        db_conn.execute("ROLLBACK TO SAVEPOINT probe")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_supabase_api_roles_cannot_create_tables(db_conn: psycopg.Connection, role: str) -> None:
    db_conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db_conn.execute("CREATE TABLE intruder (id int)")


def test_rls_enabled_on_every_table(db_conn: psycopg.Connection) -> None:
    rows = db_conn.execute(
        "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity"
    ).fetchall()

    assert rows == []


def test_hot_query_indexes_exist(db_conn: psycopg.Connection) -> None:
    rows = db_conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    names = {row[0] for row in rows}

    assert {
        "processing_jobs_claim_idx",
        "clips_review_status_idx",
        "videos_published_at_idx",
    } <= names


def setting(conn: psycopg.Connection, key: str) -> object:
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
    assert row is not None, key
    return row[0]


def test_affiliate_links_disabled_by_default(db_conn: psycopg.Connection) -> None:
    row = db_conn.execute(
        "SELECT jsonb_typeof(value), value = 'false'::jsonb FROM app_settings "
        "WHERE key = 'affiliate_links_enabled'"
    ).fetchone()

    assert row == ("boolean", True)


def test_default_settings_are_seeded(db_conn: psycopg.Connection) -> None:
    assert setting(db_conn, "min_score") == 75
    assert setting(db_conn, "clip_min_s") == 20
    assert setting(db_conn, "clip_max_s") == 60
    assert setting(db_conn, "max_clips") == 3
    assert setting(db_conn, "max_overlap") == 0.2
    assert setting(db_conn, "affiliate_min_relevance") == 0.7
    assert setting(db_conn, "reserved_disk_bytes") == 5 * 1024**3
    assert setting(db_conn, "permitted_download_domains") == []
    assert setting(db_conn, "audio_chunk_minutes") == 20
    assert "commission" in str(setting(db_conn, "affiliate_disclosure_text"))
    assert setting(db_conn, "discovery") == {
        "region_code": "ID",
        "top_categories": 5,
        "chart_pages": 4,
        "per_category_results": 50,
        "max_new_eligible_per_run": 20,
        "excluded_category_ids": [],
    }
    assert setting(db_conn, "models") == {
        "transcription": "gemini-3.5-transcribe",
        "analysis": "gemini-3.5-flash-lite",
        "rerank": "gemini-3-flash-preview",
        "analysis_fallback": "openrouter:nvidia/nemotron-3.5-super-vl:free",
        "embedding": "gemini-embedding-2",
    }


def test_provider_configs_seeded_without_secrets(db_conn: psycopg.Connection) -> None:
    rows = db_conn.execute(
        "SELECT provider, model, capability, secret_ref, enabled, quota_reset, "
        "price_input_per_mtok, price_output_per_mtok, price_per_audio_minute, currency, "
        "rpm_limit > 0, rpd_limit > 0 FROM ai_provider_configs ORDER BY capability, provider"
    ).fetchall()

    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("gemini", "gemini-3.5-flash-lite", "analysis", "GEMINI_API_KEY"),
        ("openrouter", "nvidia/nemotron-3.5-super-vl:free", "analysis", "OPENROUTER_API_KEY"),
        ("gemini", "gemini-embedding-2", "embedding", "GEMINI_API_KEY"),
        ("gemini", "gemini-3-flash-preview", "rerank", "GEMINI_API_KEY"),
        ("gemini", "gemini-3.5-transcribe", "transcription", "GEMINI_API_KEY"),
    ]
    for row in rows:
        assert row[4] is True
        assert row[5] == ("utc_midnight" if row[0] == "openrouter" else "pacific_midnight")
        assert row[6] == row[7] == row[8] == 0
        assert row[9] == "USD"
        assert row[10] is True and row[11] is True


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ai_provider_configs SET price_input_per_mtok = -1",
        "UPDATE ai_provider_configs SET price_output_per_mtok = -0.01",
        "UPDATE ai_provider_configs SET price_per_audio_minute = -5",
        "UPDATE ai_provider_configs SET quota_reset = 'whenever'",
        "UPDATE ai_provider_configs SET rpm_limit = 0",
        "INSERT INTO provider_usage (provider, model, capability, success, latency_ms, "
        "estimated_cost) VALUES ('gemini', 'm', 'analysis', true, 1, -0.5)",
        "INSERT INTO provider_usage (provider, model, capability, success, latency_ms, "
        "audio_seconds) VALUES ('gemini', 'm', 'transcription', true, 1, -1)",
    ],
)
def test_cost_and_quota_constraints(db_conn: psycopg.Connection, statement: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(statement)


def test_provider_usage_records_stage_and_cost(db_conn: psycopg.Connection) -> None:
    row = db_conn.execute(
        "INSERT INTO provider_usage (provider, model, capability, success, latency_ms, stage, "
        "audio_seconds) VALUES ('gemini', 'm', 'transcription', true, 5, 'transcribed', 61.5) "
        "RETURNING estimated_cost, currency"
    ).fetchone()

    assert row == (0, "USD")


def test_clip_title_columns_exist(db_conn: psycopg.Connection) -> None:
    rows = db_conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE (table_name, column_name) IN "
        "(('clips', 'title'), ('clips', 'title_override'), ('clip_reviews', 'title_override'))"
    ).fetchall()

    assert len(rows) == 3
