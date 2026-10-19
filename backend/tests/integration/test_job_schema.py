import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb


def insert_video(conn: psycopg.Connection) -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO videos (youtube_video_id, title, channel_id) VALUES (%s, 't', 'c') "
        "RETURNING id",
        (uuid.uuid4().hex[:11],),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def insert_job(
    conn: psycopg.Connection,
    video_id: uuid.UUID,
    *,
    status: str = "queued",
    idempotency_key: str | None = None,
) -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO processing_jobs (video_id, status, idempotency_key) "
        "VALUES (%s, %s, %s) RETURNING id",
        (video_id, status, idempotency_key or uuid.uuid4().hex),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def test_new_job_defaults(db_conn: psycopg.Connection) -> None:
    job_id = insert_job(db_conn, insert_video(db_conn))

    row = db_conn.execute(
        "SELECT stage, status, attempts, max_attempts, lease_version, lease_token, "
        "cancel_requested, settings_snapshot, next_attempt_at <= now() "
        "FROM processing_jobs WHERE id = %s",
        (job_id,),
    ).fetchone()

    assert row == ("queued", "queued", 0, 3, 0, None, False, {}, True)


@pytest.mark.parametrize("active", ["awaiting_source", "queued", "running", "retry_scheduled"])
def test_second_active_job_for_same_video_fails(db_conn: psycopg.Connection, active: str) -> None:
    video_id = insert_video(db_conn)
    insert_job(db_conn, video_id, status=active)

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_job(db_conn, video_id, status="queued")


@pytest.mark.parametrize("finished", ["completed", "completed_no_clips", "failed", "cancelled"])
def test_new_job_allowed_after_previous_finished(
    db_conn: psycopg.Connection, finished: str
) -> None:
    video_id = insert_video(db_conn)
    insert_job(db_conn, video_id, status=finished)

    insert_job(db_conn, video_id, status="queued")


def test_duplicate_idempotency_key_fails(db_conn: psycopg.Connection) -> None:
    insert_job(db_conn, insert_video(db_conn), idempotency_key="same-key")

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_job(db_conn, insert_video(db_conn), idempotency_key="same-key")


@pytest.mark.parametrize(
    "assignment",
    [
        "status = 'paused'",
        "stage = 'dancing'",
        "attempts = -1",
        "lease_token = gen_random_uuid()",
    ],
)
def test_job_check_constraints(db_conn: psycopg.Connection, assignment: str) -> None:
    job_id = insert_job(db_conn, insert_video(db_conn))

    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(f"UPDATE processing_jobs SET {assignment} WHERE id = %s", (job_id,))


def insert_event(conn: psycopg.Connection) -> int:
    job_id = insert_job(conn, insert_video(conn))
    row = conn.execute(
        "INSERT INTO job_events (job_id, event_type, stage, status, attempt) "
        "VALUES (%s, 'claimed', 'queued', 'running', 1) RETURNING id",
        (job_id,),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def test_update_job_event_raises(db_conn: psycopg.Connection) -> None:
    event_id = insert_event(db_conn)

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db_conn.execute("UPDATE job_events SET message = 'x' WHERE id = %s", (event_id,))


def test_delete_job_event_raises(db_conn: psycopg.Connection) -> None:
    event_id = insert_event(db_conn)

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db_conn.execute("DELETE FROM job_events WHERE id = %s", (event_id,))


def insert_media_asset(conn: psycopg.Connection, **overrides: object) -> uuid.UUID:
    values: dict[str, object] = {
        "video_id": insert_video(conn),
        "kind": "upload",
        "source_url": None,
        "sha256": "a" * 64,
        "size_bytes": 10,
        "permission_evidence": Jsonb({"type": "owner", "statement": "I own it"}),
    } | overrides
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    row = conn.execute(
        f"INSERT INTO media_assets ({columns}) VALUES ({placeholders}) RETURNING id",
        tuple(values.values()),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def test_media_asset_defaults(db_conn: psycopg.Connection) -> None:
    asset_id = insert_media_asset(db_conn)

    row = db_conn.execute(
        "SELECT state, received_bytes FROM media_assets WHERE id = %s", (asset_id,)
    ).fetchone()

    assert row == ("pending", 0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "torrent"},
        {"kind": "url"},
        {"source_url": "https://cdn.example.com/a.mp4"},
        {"sha256": "XYZ"},
        {"permission_evidence": Jsonb(["not", "an", "object"])},
        {"received_bytes": 11},
    ],
)
def test_media_asset_constraints(db_conn: psycopg.Connection, overrides: dict[str, object]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_media_asset(db_conn, **overrides)


def insert_clip(conn: psycopg.Connection) -> uuid.UUID:
    video_id = insert_video(conn)
    job_id = insert_job(conn, video_id)
    candidate = conn.execute(
        "INSERT INTO clip_candidates (job_id, start_ms, end_ms, hook_score, emotion_score, "
        "clarity_score, relevance_score, visual_score, completeness_score, total_score) "
        "VALUES (%s, 0, 30000, 80, 80, 80, 80, 80, 80, 80) RETURNING id",
        (job_id,),
    ).fetchone()
    assert candidate is not None
    clip = conn.execute(
        "INSERT INTO clips (candidate_id, job_id, video_id, start_ms, end_ms) "
        "VALUES (%s, %s, %s, 0, 30000) RETURNING id",
        (candidate[0], job_id, video_id),
    ).fetchone()
    assert clip is not None
    return clip[0]  # type: ignore[no-any-return]


def insert_artifact(conn: psycopg.Connection, clip_id: uuid.UUID, kind: str = "video") -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO clip_artifacts (clip_id, kind, local_path) VALUES (%s, %s, '/w/c.mp4') "
        "RETURNING id",
        (clip_id, kind),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def test_one_artifact_per_clip_and_kind(db_conn: psycopg.Connection) -> None:
    clip_id = insert_clip(db_conn)
    insert_artifact(db_conn, clip_id)

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_artifact(db_conn, clip_id)


@pytest.mark.parametrize(
    "assignment",
    [
        "upload_state = 'verified'",
        "local_deleted_at = now()",
        "md5 = 'nothex'",
    ],
)
def test_artifact_upload_invariants(db_conn: psycopg.Connection, assignment: str) -> None:
    artifact_id = insert_artifact(db_conn, insert_clip(db_conn))

    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(f"UPDATE clip_artifacts SET {assignment} WHERE id = %s", (artifact_id,))


def test_local_copy_may_be_deleted_after_verified_upload(db_conn: psycopg.Connection) -> None:
    artifact_id = insert_artifact(db_conn, insert_clip(db_conn))

    db_conn.execute(
        "UPDATE clip_artifacts SET upload_state = 'verified', drive_file_id = 'f1', "
        "local_deleted_at = now() WHERE id = %s",
        (artifact_id,),
    )
