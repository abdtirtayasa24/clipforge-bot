import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import insert, update

from clipforge.checksums import file_sha256
from clipforge.db.models import MediaAsset, ProcessingJob
from clipforge.media.audio import AudioManifest
from clipforge.media.probe import probe
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stages.audio import MANIFEST_NAME, AudioStage
from clipforge.queue.errors import PermanentError
from clipforge.queue.repository import JobQueue
from tests.fixtures.make_media import make_media
from tests.jobs import Sessions, claim, make_video, new_key

pytestmark = pytest.mark.usefixtures("require_ffmpeg")

# Gemini's inline request limit; larger chunks would need the Files API.
PROVIDER_INLINE_LIMIT_BYTES = 20 * 1024 * 1024


@pytest.fixture(scope="session")
def source_25min(media_dir: Path, require_ffmpeg: None) -> Path:
    return make_media(media_dir / "talk-25min.mp4", duration_s=25 * 60)


async def context_for(
    queue: JobQueue,
    sessions: Sessions,
    source: Path,
    workspace: Path,
    snapshot: dict[str, object],
) -> StageContext:
    video_id = await make_video(sessions)
    async with sessions.begin() as session:
        asset_id = await session.scalar(
            insert(MediaAsset)
            .values(
                video_id=video_id,
                kind="upload",
                permission_evidence={},
                state="ready",
                local_path=str(source),
                sha256=file_sha256(source),
                media_info=(await probe(source)).model_dump(mode="json"),
            )
            .returning(MediaAsset.id)
        )
    job, _ = await queue.enqueue(video_id, new_key(), media_asset_id=asset_id)
    async with sessions.begin() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job.id)
            .values(settings_snapshot=snapshot, stage="validated")
        )
    workspace.mkdir(parents=True, exist_ok=True)
    return StageContext(job=await claim(queue), queue=queue, workspace=workspace)


def load_manifest(workspace: Path) -> AudioManifest:
    return AudioManifest.model_validate_json((workspace / "audio" / MANIFEST_NAME).read_text())


async def test_25_minute_source_becomes_three_overlapping_chunks(
    queue: JobQueue, sessions: Sessions, source_25min: Path, tmp_path: Path
) -> None:
    ctx = await context_for(queue, sessions, source_25min, tmp_path, {"audio_chunk_minutes": 10})

    result = await AudioStage(sessions).run(ctx)

    manifest = load_manifest(tmp_path)
    assert result.details["chunks"] == 3
    assert result.details["reused"] is False
    assert manifest.source_sha256 == file_sha256(source_25min)
    assert [(c.start_s, c.end_s) for c in manifest.chunks] == [
        (0, 602),
        (600, 1202),
        (1200, 1500),
    ]
    for chunk in manifest.chunks:
        path = tmp_path / "audio" / chunk.file
        info = await probe(path)
        assert info.duration_s == pytest.approx(chunk.end_s - chunk.start_s, abs=0.2)
        assert (info.audio_channels, info.audio_sample_rate) == (1, 16000)
        assert info.video_codec is None
        assert chunk.sha256 == file_sha256(path)
        assert chunk.size_bytes == path.stat().st_size < PROVIDER_INLINE_LIMIT_BYTES


async def test_default_chunk_length_is_20_minutes(
    queue: JobQueue, sessions: Sessions, source_25min: Path, tmp_path: Path
) -> None:
    ctx = await context_for(queue, sessions, source_25min, tmp_path, {})

    await AudioStage(sessions).run(ctx)

    chunks = load_manifest(tmp_path).chunks
    assert [(c.start_s, c.end_s) for c in chunks] == [(0, 1202), (1200, 1500)]


async def test_rerun_reuses_verified_chunks(
    queue: JobQueue, sessions: Sessions, source_25min: Path, tmp_path: Path
) -> None:
    ctx = await context_for(queue, sessions, source_25min, tmp_path, {"audio_chunk_minutes": 10})
    stage = AudioStage(sessions)
    await stage.run(ctx)
    files = sorted((tmp_path / "audio").iterdir())
    modified = {path: path.stat().st_mtime_ns for path in files}

    result = await stage.run(ctx)

    assert result.details["reused"] is True
    assert {path: path.stat().st_mtime_ns for path in files} == modified


async def test_corrupted_chunk_is_extracted_again(
    queue: JobQueue, sessions: Sessions, source_25min: Path, tmp_path: Path
) -> None:
    ctx = await context_for(queue, sessions, source_25min, tmp_path, {"audio_chunk_minutes": 10})
    stage = AudioStage(sessions)
    await stage.run(ctx)
    first = load_manifest(tmp_path)
    (tmp_path / "audio" / first.chunks[1].file).write_bytes(b"truncated")

    result = await stage.run(ctx)

    assert result.details["reused"] is False
    assert load_manifest(tmp_path).chunks == first.chunks


async def test_changed_chunk_length_rebuilds_the_manifest(
    queue: JobQueue, sessions: Sessions, source_25min: Path, tmp_path: Path
) -> None:
    ctx = await context_for(queue, sessions, source_25min, tmp_path, {"audio_chunk_minutes": 10})
    await AudioStage(sessions).run(ctx)
    manifest_path = tmp_path / "audio" / MANIFEST_NAME
    stale = json.loads(manifest_path.read_text())
    stale["chunk_s"] = 1200
    manifest_path.write_text(json.dumps(stale))

    result = await AudioStage(sessions).run(ctx)

    assert result.details["reused"] is False
    assert load_manifest(tmp_path).chunk_s == 600


async def test_missing_source_file_fails_permanently(
    queue: JobQueue, sessions: Sessions, source_25min: Path, tmp_path: Path
) -> None:
    ctx = await context_for(queue, sessions, source_25min, tmp_path, {})
    async with sessions.begin() as session:
        await session.execute(
            update(MediaAsset)
            .where(MediaAsset.id == ctx.job.media_asset_id)
            .values(local_path=str(tmp_path / f"{uuid.uuid4()}.mp4"))
        )

    with pytest.raises(PermanentError, match="source"):
        await AudioStage(sessions).run(ctx)
