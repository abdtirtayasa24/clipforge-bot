import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
import respx
from psycopg.types.json import Jsonb

from clipforge.ingest.download import download
from clipforge.ingest.uploads import source_path
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.runner import PipelineRunner
from clipforge.pipeline.stages.audio import AudioStage
from clipforge.pipeline.stages.ingest import IngestStage
from clipforge.queue.errors import PermanentError, TransientError
from clipforge.queue.repository import JobQueue
from tests.db import libpq_url
from tests.jobs import Sessions, claim, stage_events
from tests.youtube import MONITORED_CHANNEL

pytestmark = pytest.mark.security

ALLOW = ["media.example.com", "*.cdn.example.com"]
URL = "https://media.example.com/source.mp4"
EVIDENCE = {
    "type": "creator_permission",
    "statement": "The creator granted written permission to clip this video.",
}


@pytest.fixture
def http() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


async def fetch(dest: Path, url: str = URL, max_bytes: int = 10_000) -> Any:
    return await download(url, dest, allowlist=ALLOW, max_bytes=max_bytes, transport=None)


async def test_download_streams_to_disk_with_checksum(
    http: respx.MockRouter, tmp_path: Path
) -> None:
    data = b"media-bytes" * 100
    http.get(URL).respond(200, content=data)
    dest = tmp_path / "source"

    result = await fetch(dest)

    assert (result.sha256, result.size_bytes) == (hashlib.sha256(data).hexdigest(), len(data))
    assert dest.read_bytes() == data
    assert list(tmp_path.iterdir()) == [dest]


async def test_redirects_to_permitted_hosts_are_followed(
    http: respx.MockRouter, tmp_path: Path
) -> None:
    http.get(URL).respond(302, headers={"Location": "https://edge.cdn.example.com/a.mp4"})
    http.get("https://edge.cdn.example.com/a.mp4").respond(200, content=b"ok")

    result = await fetch(tmp_path / "source")

    assert result.size_bytes == 2


@pytest.mark.parametrize(
    "location",
    [
        "https://127.0.0.1/a.mp4",
        "https://[::1]/a.mp4",
        "http://media.example.com/a.mp4",
        "https://evil.example.net/a.mp4",
        "https://media.example.com.evil.net/a.mp4",
        "https://www.youtube.com/watch?v=abc",
        "file:///etc/passwd",
    ],
)
async def test_redirects_to_unsafe_targets_are_rejected(
    http: respx.MockRouter, tmp_path: Path, location: str
) -> None:
    http.get(URL).respond(302, headers={"Location": location})

    with pytest.raises(PermanentError):
        await fetch(tmp_path / "source")

    assert list(tmp_path.iterdir()) == []


async def test_redirect_loops_are_bounded(http: respx.MockRouter, tmp_path: Path) -> None:
    http.get(URL).respond(302, headers={"Location": URL})

    with pytest.raises(PermanentError, match="redirects"):
        await fetch(tmp_path / "source")


async def test_declared_oversize_download_is_refused(
    http: respx.MockRouter, tmp_path: Path
) -> None:
    http.get(URL).respond(200, content=b"x" * 20_000)

    with pytest.raises(PermanentError, match="exceeds"):
        await fetch(tmp_path / "source")

    assert list(tmp_path.iterdir()) == []


async def test_streamed_oversize_download_aborts_and_cleans_up(
    http: respx.MockRouter, tmp_path: Path
) -> None:
    async def body() -> Any:
        for _ in range(30):
            yield b"x" * 1000

    http.get(URL).mock(return_value=httpx.Response(200, content=body()))

    with pytest.raises(PermanentError, match="exceeds"):
        await fetch(tmp_path / "source")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(404), PermanentError),
        (httpx.Response(403), PermanentError),
        (httpx.Response(503), TransientError),
        (httpx.Response(429), TransientError),
        (httpx.ConnectError("refused"), TransientError),
        (httpx.ReadTimeout("slow"), TransientError),
    ],
)
async def test_http_failures_map_to_the_error_taxonomy(
    http: respx.MockRouter, tmp_path: Path, response: Any, error: type[Exception]
) -> None:
    http.get(URL).mock(side_effect=[response])

    with pytest.raises(error):
        await fetch(tmp_path / "source")

    assert list(tmp_path.iterdir()) == []


async def test_disallowed_source_url_is_rejected_without_a_request(
    http: respx.MockRouter, tmp_path: Path
) -> None:
    with pytest.raises(PermanentError, match="YouTube"):
        await fetch(tmp_path / "source", url="https://youtu.be/abc")

    assert not http.calls


# --- ingest stage -------------------------------------------------------------------------


@pytest.fixture
def permitted_domains(migrated_database_url: str) -> Iterator[None]:
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        conn.execute(
            "UPDATE app_settings SET value = %s WHERE key = 'permitted_download_domains'",
            (Jsonb(ALLOW),),
        )
        yield
        conn.execute(
            "UPDATE app_settings SET value = '[]' WHERE key = 'permitted_download_domains'"
        )


def insert_asset(url: str, **values: Any) -> tuple[uuid.UUID, uuid.UUID]:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        video = conn.execute(
            "INSERT INTO videos (youtube_video_id, title, channel_id) VALUES (%s, 't', %s) "
            "RETURNING id",
            (uuid.uuid4().hex[:11], MONITORED_CHANNEL),
        ).fetchone()
        assert video is not None
        columns = {"video_id": video[0], "permission_evidence": Jsonb(EVIDENCE)} | values
        asset = conn.execute(
            f"INSERT INTO media_assets ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING id",
            tuple(columns.values()),
        ).fetchone()
        assert asset is not None
    return video[0], asset[0]


def asset_state(url: str, asset_id: uuid.UUID) -> tuple[Any, ...]:
    with psycopg.connect(libpq_url(url)) as conn:
        row = conn.execute(
            "SELECT m.state, m.sha256, m.size_bytes, m.local_path, m.failure_reason, "
            "v.rights_status FROM media_assets m JOIN videos v ON v.id = m.video_id "
            "WHERE m.id = %s",
            (asset_id,),
        ).fetchone()
    assert row is not None
    return tuple(row)


async def stage_context(
    queue: JobQueue, video_id: uuid.UUID, asset_id: uuid.UUID, workspace: Path
) -> StageContext:
    await queue.enqueue(video_id, uuid.uuid4().hex, media_asset_id=asset_id)
    return StageContext(job=await claim(queue), queue=queue, workspace=workspace / "job")


@pytest.mark.usefixtures("permitted_domains")
async def test_ingest_stage_downloads_validates_and_authorizes(
    migrated_database_url: str,
    queue: JobQueue,
    sessions: Sessions,
    http: respx.MockRouter,
    valid_source: Path,
    tmp_path: Path,
) -> None:
    data = valid_source.read_bytes()
    route = http.get(URL).respond(200, content=data)
    video_id, asset_id = insert_asset(
        migrated_database_url, kind="url", source_url=URL, state="pending"
    )
    stage = IngestStage(sessions, workspace_root=tmp_path)
    context = await stage_context(queue, video_id, asset_id, tmp_path)

    result = await stage.run(context)

    state, sha256, size, local_path, _, rights = asset_state(migrated_database_url, asset_id)
    assert (state, sha256, size, rights) == (
        "ready",
        hashlib.sha256(data).hexdigest(),
        len(data),
        "authorized",
    )
    assert Path(local_path) == source_path(tmp_path, asset_id)
    assert result.details["downloaded"] is True

    again = await stage.run(context)

    assert route.call_count == 1
    assert again.details["downloaded"] is False


@pytest.mark.usefixtures("permitted_domains")
async def test_ingest_stage_rejects_invalid_downloaded_media(
    migrated_database_url: str,
    queue: JobQueue,
    sessions: Sessions,
    http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    http.get(URL).respond(200, content=b"not a video" * 100)
    video_id, asset_id = insert_asset(
        migrated_database_url, kind="url", source_url=URL, state="pending"
    )
    context = await stage_context(queue, video_id, asset_id, tmp_path)

    with pytest.raises(PermanentError, match="unreadable"):
        await IngestStage(sessions, workspace_root=tmp_path).run(context)

    state, _, _, _, reason, rights = asset_state(migrated_database_url, asset_id)
    assert (state, rights) == ("failed", "unverified")
    assert "unreadable" in reason
    assert not source_path(tmp_path, asset_id).exists()


async def test_ingest_stage_refuses_urls_once_the_host_is_no_longer_permitted(
    migrated_database_url: str,
    queue: JobQueue,
    sessions: Sessions,
    http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    video_id, asset_id = insert_asset(
        migrated_database_url, kind="url", source_url=URL, state="pending"
    )
    context = await stage_context(queue, video_id, asset_id, tmp_path)

    with pytest.raises(PermanentError, match="disabled"):
        await IngestStage(sessions, workspace_root=tmp_path).run(context)

    assert not http.calls


async def test_ingest_stage_verifies_uploaded_sources(
    migrated_database_url: str,
    queue: JobQueue,
    sessions: Sessions,
    valid_source: Path,
    tmp_path: Path,
) -> None:
    data = valid_source.read_bytes()
    path = tmp_path / "upload.mp4"
    path.write_bytes(data)
    video_id, asset_id = insert_asset(
        migrated_database_url,
        kind="upload",
        state="ready",
        local_path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )
    stage = IngestStage(sessions, workspace_root=tmp_path)
    context = await stage_context(queue, video_id, asset_id, tmp_path)

    assert (await stage.run(context)).details["downloaded"] is False

    path.write_bytes(data[:-1])
    with pytest.raises(PermanentError, match="checksum"):
        await stage.run(context)


@pytest.mark.usefixtures("permitted_domains")
async def test_upload_and_url_sources_go_through_ingest_and_audio(
    migrated_database_url: str,
    queue: JobQueue,
    sessions: Sessions,
    http: respx.MockRouter,
    valid_source: Path,
    tmp_path: Path,
) -> None:
    data = valid_source.read_bytes()
    http.get(URL).respond(200, content=data)
    uploaded = tmp_path / "uploaded.mp4"
    uploaded.write_bytes(data)
    sources = [
        insert_asset(migrated_database_url, kind="url", source_url=URL, state="pending"),
        insert_asset(
            migrated_database_url,
            kind="upload",
            state="ready",
            local_path=str(uploaded),
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        ),
    ]
    stages = [IngestStage(sessions, workspace_root=tmp_path), AudioStage(sessions)]
    runner = PipelineRunner(queue, stages, workspace_root=tmp_path)

    for video_id, asset_id in sources:
        job, _ = await queue.enqueue(video_id, uuid.uuid4().hex, media_asset_id=asset_id)
        assert await runner.run(await claim(queue)) == "completed"
        stages = await stage_events(sessions, job.id)
        assert stages == ["ingesting", "validated", "audio_extracted"]
        assert asset_state(migrated_database_url, asset_id)[0] == "ready"
