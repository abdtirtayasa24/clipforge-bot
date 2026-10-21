import hashlib
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from clipforge.ingest import disk
from tests.api import admin_client
from tests.db import libpq_url
from tests.fixtures.make_media import make_media

UPLOADS = "/api/v1/uploads"
MIB = 1024 * 1024
GIB = 1024 * MIB


class FakeUsage(NamedTuple):
    total: int
    used: int
    free: int


def fake_disk(free: int, total: int = 100 * GIB) -> Callable[[object], FakeUsage]:
    return lambda _path: FakeUsage(total=total, used=total - free, free=free)


@pytest.fixture(autouse=True)
def plenty_of_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk.shutil, "disk_usage", fake_disk(50 * GIB))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def client(migrated_database_url: str, workspace: Path) -> Iterator[TestClient]:
    with admin_client(migrated_database_url, workspace_dir=workspace) as test_client:
        yield test_client


@pytest.fixture
def video_id(migrated_database_url: str) -> str:
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO videos (youtube_video_id, title, channel_id) VALUES (%s, 'v', 'UCx') "
            "RETURNING id",
            (uuid.uuid4().hex[:11],),
        ).fetchone()
    assert row is not None
    return str(row[0])


def video_row(url: str, video_id: str) -> tuple[Any, ...]:
    with psycopg.connect(libpq_url(url)) as conn:
        row = conn.execute("SELECT rights_status FROM videos WHERE id = %s", (video_id,)).fetchone()
    assert row is not None
    return tuple(row)


def asset_row(url: str, asset_id: str) -> dict[str, Any]:
    with psycopg.connect(libpq_url(url)) as conn:
        cursor = conn.execute(
            "SELECT kind, state, local_path, original_filename, sha256, size_bytes, "
            "received_bytes, permission_evidence, media_info, failure_reason "
            "FROM media_assets WHERE id = %s",
            (asset_id,),
        )
        row = cursor.fetchone()
        assert row is not None and cursor.description is not None
        return {column.name: value for column, value in zip(cursor.description, row, strict=True)}


def init_upload(client: TestClient, video_id: str, data: bytes, **overrides: object) -> Response:
    body: dict[str, object] = {
        "video_id": video_id,
        "filename": "interview.mp4",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    merged = body | overrides
    return client.post(
        UPLOADS, json={key: value for key, value in merged.items() if value is not None}
    )


def send_chunk(
    client: TestClient, upload_id: str, data: bytes, start: int, total: int | None = None
) -> Response:
    end = start + len(data) - 1
    return client.patch(
        f"{UPLOADS}/{upload_id}",
        content=data,
        headers={
            "Content-Range": f"bytes {start}-{end}/{total if total is not None else len(data)}",
            "Content-Type": "application/octet-stream",
        },
    )


def upload_all(client: TestClient, upload_id: str, data: bytes, chunk: int = 2 * MIB) -> None:
    for start in range(0, len(data), chunk):
        response = send_chunk(client, upload_id, data[start : start + chunk], start, len(data))
        assert response.status_code == 200, response.text


def created(response: Response) -> str:
    assert response.status_code == 201, response.text
    upload_id: str = response.json()["id"]
    return upload_id


def test_chunked_upload_completes_and_authorizes_the_video(
    client: TestClient,
    migrated_database_url: str,
    valid_source: Path,
    video_id: str,
    workspace: Path,
) -> None:
    data = valid_source.read_bytes()
    upload_id = created(init_upload(client, video_id, data))

    upload_all(client, upload_id, data)
    response = client.post(f"{UPLOADS}/{upload_id}/complete")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["received_bytes"], body["size_bytes"]) == (
        "ready",
        len(data),
        len(data),
    )
    asset = asset_row(migrated_database_url, upload_id)
    assert asset["kind"] == "upload"
    assert asset["sha256"] == hashlib.sha256(data).hexdigest()
    assert asset["media_info"]["video_codec"] == "h264"
    assert set(asset["permission_evidence"]) == {"recorded_by", "recorded_at"}
    source = Path(asset["local_path"])
    assert source == workspace / "sources" / upload_id
    assert source.read_bytes() == data
    assert not (workspace / "uploads" / upload_id).exists()
    assert video_row(migrated_database_url, video_id) == ("authorized",)


def test_interrupted_upload_resumes_from_server_offset(
    client: TestClient, migrated_database_url: str, valid_source: Path, video_id: str
) -> None:
    data = valid_source.read_bytes()
    upload_id = created(init_upload(client, video_id, data))
    assert send_chunk(client, upload_id, data[: 2 * MIB], 0, len(data)).status_code == 200

    replay = send_chunk(client, upload_id, data[: 2 * MIB], 0, len(data))
    status = client.get(f"{UPLOADS}/{upload_id}").json()

    assert replay.status_code == 409
    assert replay.json()["error"]["details"] == {"received_bytes": 2 * MIB}
    assert status["received_bytes"] == 2 * MIB
    for start in range(status["received_bytes"], len(data), 2 * MIB):
        chunk = data[start : start + 2 * MIB]
        assert send_chunk(client, upload_id, chunk, start, len(data)).status_code == 200
    assert client.post(f"{UPLOADS}/{upload_id}/complete").status_code == 200


def test_wrong_checksum_fails_and_removes_staging(
    client: TestClient,
    migrated_database_url: str,
    valid_source: Path,
    video_id: str,
    workspace: Path,
) -> None:
    data = valid_source.read_bytes()
    upload_id = created(init_upload(client, video_id, data, sha256="0" * 64))
    upload_all(client, upload_id, data)

    response = client.post(f"{UPLOADS}/{upload_id}/complete")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "checksum_mismatch"
    asset = asset_row(migrated_database_url, upload_id)
    assert asset["state"] == "failed"
    assert not (workspace / "uploads" / upload_id).exists()
    assert video_row(migrated_database_url, video_id) == ("unverified",)
    assert send_chunk(client, upload_id, data[:1], 0, len(data)).status_code == 409


def test_invalid_media_fails_with_reason_and_removes_staging(
    client: TestClient,
    migrated_database_url: str,
    media_dir: Path,
    video_id: str,
    workspace: Path,
) -> None:
    data = make_media(media_dir / "too-short.mp4", duration_s=10).read_bytes()
    upload_id = created(init_upload(client, video_id, data))
    upload_all(client, upload_id, data)

    response = client.post(f"{UPLOADS}/{upload_id}/complete")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_media"
    assert "duration" in response.json()["error"]["message"]
    asset = asset_row(migrated_database_url, upload_id)
    assert asset["state"] == "failed"
    assert "duration" in asset["failure_reason"]
    assert not (workspace / "uploads" / upload_id).exists()


def test_complete_before_all_bytes_arrived_is_a_conflict(client: TestClient, video_id: str) -> None:
    data = b"x" * 1000
    upload_id = created(init_upload(client, video_id, data))
    assert send_chunk(client, upload_id, data[:500], 0, len(data)).status_code == 200

    response = client.post(f"{UPLOADS}/{upload_id}/complete")

    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"received_bytes": 500}


def test_oversize_chunk_is_rejected(client: TestClient, video_id: str) -> None:
    size = 9 * MIB
    upload_id = created(init_upload(client, video_id, b"\0" * size))

    response = send_chunk(client, upload_id, b"\0" * (8 * MIB + 1), 0, size)

    assert response.status_code == 413
    assert client.get(f"{UPLOADS}/{upload_id}").json()["received_bytes"] == 0


@pytest.mark.parametrize(
    "content_range",
    ["bytes 0-9/999", "bytes 0-8/10", "bytes 5-0/10", "items 0-9/10", "bytes 0-9/*", None],
)
def test_bad_content_range_is_rejected(
    client: TestClient, video_id: str, content_range: str | None
) -> None:
    upload_id = created(init_upload(client, video_id, b"x" * 10))
    headers = {"Content-Range": content_range} if content_range else {}

    response = client.patch(f"{UPLOADS}/{upload_id}", content=b"x" * 10, headers=headers)

    assert response.status_code == 400


def test_chunk_beyond_declared_size_is_rejected(client: TestClient, video_id: str) -> None:
    upload_id = created(init_upload(client, video_id, b"x" * 10))

    response = client.patch(
        f"{UPLOADS}/{upload_id}", content=b"x" * 11, headers={"Content-Range": "bytes 0-10/10"}
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": "abc"},
        {"size_bytes": 0},
        {"filename": ""},
    ],
)
def test_init_requires_valid_metadata(
    client: TestClient, video_id: str, overrides: dict[str, object]
) -> None:
    assert init_upload(client, video_id, b"x" * 10, **overrides).status_code == 422


def test_unknown_video_is_not_found(client: TestClient) -> None:
    response = init_upload(client, str(uuid.uuid4()), b"x" * 10)

    assert response.status_code == 404


def test_staging_uses_uuid_names_never_the_filename(
    client: TestClient, migrated_database_url: str, video_id: str, workspace: Path
) -> None:
    upload_id = created(init_upload(client, video_id, b"x" * 10, filename="../../etc/passwd"))
    assert send_chunk(client, upload_id, b"x" * 10, 0).status_code == 200

    asset = asset_row(migrated_database_url, upload_id)
    staging = workspace / "uploads" / upload_id
    assert Path(asset["local_path"]) == staging
    assert staging.read_bytes() == b"x" * 10
    assert asset["original_filename"] == "../../etc/passwd"
    assert {path.name for path in workspace.rglob("*")} == {"uploads", upload_id}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_staging_directory_is_private(client: TestClient, video_id: str, workspace: Path) -> None:
    created(init_upload(client, video_id, b"x" * 10))

    assert (workspace / "uploads").stat().st_mode & 0o777 == 0o700


def test_disk_admission_rejects_or_defers(
    client: TestClient, video_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(disk.shutil, "disk_usage", fake_disk(free=6 * GIB, total=20 * GIB))
    deferred = init_upload(client, video_id, b"", size_bytes=2 * GIB, sha256="0" * 64)
    rejected = init_upload(client, video_id, b"", size_bytes=19 * GIB, sha256="0" * 64)

    assert deferred.status_code == 507
    assert deferred.json()["error"]["code"] == "insufficient_storage"
    assert rejected.status_code == 413
