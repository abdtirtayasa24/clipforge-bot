import hashlib
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import delete, insert, select, update

from clipforge.ai.registry import snapshot_job_settings
from clipforge.db.models import AppSetting, Clip, ClipArtifact, ClipCandidate, ProcessingJob
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stages.render import CLIPS_DIR, drive_file_name
from clipforge.pipeline.stages.upload import UploadStage
from clipforge.queue.errors import TransientError
from clipforge.queue.repository import JobQueue
from clipforge.storage.drive import CLIP_ID_PROPERTY, MIN_CHUNK_BYTES, DriveClient
from clipforge.storage.drive_oauth import FOLDER_KEY, DriveNotConnected
from tests.clock import FakeClock
from tests.drive import FakeDrive, FakeTokens
from tests.jobs import Sessions, claim, make_queued_job

CLIP_BYTES = MIN_CHUNK_BYTES * 2 + 1000
TITLES = ["Judul: satu", "Judul dua"]


@pytest.fixture
def drive() -> Iterator[FakeDrive]:
    fake = FakeDrive()
    with respx.mock(base_url="https://www.googleapis.com", assert_all_called=False) as router:
        router.route().mock(side_effect=fake.handle)
        yield fake


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
async def stage(sessions: Sessions, http: httpx.AsyncClient) -> AsyncIterator[UploadStage]:
    async with sessions.begin() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key == FOLDER_KEY))
    yield UploadStage(
        sessions, DriveClient(http, FakeTokens(), sessions, chunk_bytes=MIN_CHUNK_BYTES)
    )
    async with sessions.begin() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key == FOLDER_KEY))


@pytest.fixture
async def ctx(sessions: Sessions, clock: FakeClock, tmp_path: Path) -> StageContext:
    queue = JobQueue(
        sessions, clock=clock, jitter=lambda: 0.0, snapshot_settings=snapshot_job_settings
    )
    job_id = await make_queued_job(queue, sessions)
    clips_dir = tmp_path / CLIPS_DIR
    clips_dir.mkdir()
    async with sessions.begin() as session:
        job = await session.get_one(ProcessingJob, job_id)
        job.stage = "rendering"
        for index, title in enumerate(TITLES):
            candidate_id = await session.scalar(
                insert(ClipCandidate)
                .values(
                    job_id=job_id,
                    start_ms=index * 100_000,
                    end_ms=index * 100_000 + 30_000,
                    hook_score=90,
                    emotion_score=90,
                    clarity_score=90,
                    relevance_score=90,
                    visual_score=90,
                    completeness_score=90,
                    total_score=90,
                    title=title,
                    selected=True,
                    rank=index + 1,
                )
                .returning(ClipCandidate.id)
            )
            clip_id = await session.scalar(
                insert(Clip)
                .values(
                    candidate_id=candidate_id,
                    job_id=job_id,
                    video_id=job.video_id,
                    start_ms=index * 100_000,
                    end_ms=index * 100_000 + 30_000,
                    title=title,
                )
                .returning(Clip.id)
            )
            path = clips_dir / f"{clip_id}.mp4"
            data = os.urandom(CLIP_BYTES)
            path.write_bytes(data)
            await session.execute(
                insert(ClipArtifact).values(
                    clip_id=clip_id,
                    kind="video",
                    local_path=str(path),
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    md5=hashlib.md5(data, usedforsecurity=False).hexdigest(),
                )
            )
    return StageContext(job=await claim(queue), queue=queue, workspace=tmp_path)


async def artifacts(sessions: Sessions, job_id: uuid.UUID) -> list[tuple[Clip, ClipArtifact]]:
    async with sessions() as session:
        rows = await session.execute(
            select(Clip, ClipArtifact)
            .join(ClipArtifact, ClipArtifact.clip_id == Clip.id)
            .where(Clip.job_id == job_id)
            .order_by(Clip.start_ms)
        )
        return [(clip, artifact) for clip, artifact in rows]


def session_starts(drive: FakeDrive) -> int:
    return sum(
        1
        for request in drive.requests
        if request.method == "POST" and request.url.path == "/upload/drive/v3/files"
    )


async def test_clips_are_uploaded_verified_and_then_deleted_locally(
    sessions: Sessions, ctx: StageContext, stage: UploadStage, drive: FakeDrive
) -> None:
    local = {
        clip.id: Path(artifact.local_path or "").read_bytes()
        for clip, artifact in await artifacts(sessions, ctx.job.lease.job_id)
    }

    result = await stage.run(ctx)

    assert result.details == {"clips": 2, "uploaded": 2, "adopted": 0, "already_verified": 0}
    [folder] = drive.live_files(clipforge_folder="clips")
    for clip, artifact in await artifacts(sessions, ctx.job.lease.job_id):
        [remote] = drive.live_files(**{CLIP_ID_PROPERTY: str(clip.id)})
        assert drive.content[remote["id"]] == local[clip.id]
        assert remote["name"] == drive_file_name(clip.title, clip.id)
        assert remote["parents"] == [folder["id"]]
        assert (artifact.upload_state, artifact.drive_file_id) == ("verified", remote["id"])
        assert (clip.drive_file_id, clip.drive_web_view_link) == (
            remote["id"],
            remote["webViewLink"],
        )
        assert artifact.local_deleted_at is not None
        assert not Path(artifact.local_path or "").exists()
    assert not any("permissions" in request.url.path for request in drive.requests)


async def test_crash_after_drive_completed_adopts_the_file_without_a_duplicate(
    sessions: Sessions, ctx: StageContext, stage: UploadStage, drive: FakeDrive
) -> None:
    drive.crash_on_complete = True
    with pytest.raises(TransientError):
        await stage.run(ctx)
    (first, artifact), _ = await artifacts(sessions, ctx.job.lease.job_id)
    assert artifact.upload_state == "uploading"
    assert Path(artifact.local_path or "").exists()
    assert len(drive.live_files(**{CLIP_ID_PROPERTY: str(first.id)})) == 1
    drive.crash_on_complete = False
    started = session_starts(drive)

    result = await stage.run(ctx)

    assert result.details["adopted"] == 1
    assert session_starts(drive) == started + 1, "only the second clip starts a session"
    for clip, artifact in await artifacts(sessions, ctx.job.lease.job_id):
        assert len(drive.live_files(**{CLIP_ID_PROPERTY: str(clip.id)})) == 1
        assert artifact.upload_state == "verified"


async def test_crash_mid_upload_resumes_from_the_confirmed_offset(
    sessions: Sessions, ctx: StageContext, stage: UploadStage, drive: FakeDrive
) -> None:
    drive.crash_after_chunks = 1
    with pytest.raises(TransientError):
        await stage.run(ctx)
    drive.crash_after_chunks = None
    drive.chunk_offsets.clear()
    started = session_starts(drive)

    await stage.run(ctx)

    assert drive.chunk_offsets[0] == MIN_CHUNK_BYTES, "the first clip resumes, not restarts"
    assert session_starts(drive) == started + 1, "only the second clip starts a session"
    for clip, artifact in await artifacts(sessions, ctx.job.lease.job_id):
        [remote] = drive.live_files(**{CLIP_ID_PROPERTY: str(clip.id)})
        assert int(remote["size"]) == CLIP_BYTES
        assert artifact.upload_state == "verified"


async def test_checksum_mismatch_keeps_the_local_file_and_re_uploads(
    sessions: Sessions, ctx: StageContext, stage: UploadStage, drive: FakeDrive
) -> None:
    drive.corrupt_md5 = True
    with pytest.raises(TransientError, match="checksum"):
        await stage.run(ctx)
    (first, artifact), _ = await artifacts(sessions, ctx.job.lease.job_id)
    assert artifact.upload_state != "verified"
    assert artifact.local_deleted_at is None
    assert Path(artifact.local_path or "").exists()
    assert drive.live_files(**{CLIP_ID_PROPERTY: str(first.id)}) == []
    drive.corrupt_md5 = False

    await stage.run(ctx)

    for clip, artifact in await artifacts(sessions, ctx.job.lease.job_id):
        assert len(drive.live_files(**{CLIP_ID_PROPERTY: str(clip.id)})) == 1
        assert artifact.upload_state == "verified"


async def test_expired_upload_session_starts_a_new_one(
    sessions: Sessions, ctx: StageContext, stage: UploadStage, drive: FakeDrive
) -> None:
    (_, artifact), _ = await artifacts(sessions, ctx.job.lease.job_id)
    async with sessions.begin() as session:
        await session.execute(
            update(ClipArtifact)
            .where(ClipArtifact.id == artifact.id)
            .values(
                upload_state="uploading",
                drive_resumable_uri="https://www.googleapis.com/upload/drive/v3/files"
                "?uploadType=resumable&upload_id=gone",
            )
        )

    await stage.run(ctx)

    assert session_starts(drive) == 2
    for _, artifact in await artifacts(sessions, ctx.job.lease.job_id):
        assert artifact.upload_state == "verified"


async def test_verified_clip_with_a_leftover_local_file_is_only_cleaned_up(
    sessions: Sessions, ctx: StageContext, stage: UploadStage, drive: FakeDrive
) -> None:
    await stage.run(ctx)
    (_, artifact), _ = await artifacts(sessions, ctx.job.lease.job_id)
    # A crash between persisting the verification and deleting the local copy.
    Path(artifact.local_path or "").write_bytes(b"leftover")
    async with sessions.begin() as session:
        await session.execute(
            update(ClipArtifact).where(ClipArtifact.id == artifact.id).values(local_deleted_at=None)
        )
    drive.requests.clear()

    result = await stage.run(ctx)

    assert result.details["already_verified"] == 2
    assert not Path(artifact.local_path or "").exists()
    assert not any(request.url.path.startswith("/upload") for request in drive.requests)


async def test_without_a_drive_connection_nothing_is_touched(
    sessions: Sessions, ctx: StageContext, http: httpx.AsyncClient
) -> None:
    with pytest.raises(DriveNotConnected):
        await UploadStage(sessions, None).run(ctx)

    for _, artifact in await artifacts(sessions, ctx.job.lease.job_id):
        assert artifact.upload_state == "pending"
        assert Path(artifact.local_path or "").exists()


class StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    async def access_token(self) -> str:
        return self._token

    def invalidate(self) -> None:
        pass


@pytest.mark.live
async def test_live_resumable_upload_is_verified_and_trashed(tmp_path: Path) -> None:
    """Opt-in: `CLIPFORGE_LIVE_DRIVE_ACCESS_TOKEN=... python -m pytest -m live -k drive_upload`
    with a `drive.file` access token (for example from the OAuth 2.0 Playground)."""
    token = os.environ.get("CLIPFORGE_LIVE_DRIVE_ACCESS_TOKEN")
    if not token:
        pytest.skip("CLIPFORGE_LIVE_DRIVE_ACCESS_TOKEN is not set")
    data = os.urandom(MIN_CHUNK_BYTES + 1000)
    path = tmp_path / "clip.mp4"
    path.write_bytes(data)
    clip_id = uuid.uuid4()
    async with httpx.AsyncClient() as http:
        drive = DriveClient(http, StaticToken(token), None, chunk_bytes=MIN_CHUNK_BYTES)  # type: ignore[arg-type]
        session_uri = await drive.start_upload(
            "clipforge-live-test.mp4", "root", clip_id, len(data)
        )
        remote = await drive.upload(session_uri, path, len(data), offset=0)
        try:
            assert remote.size == len(data)
            assert remote.md5_checksum == hashlib.md5(data, usedforsecurity=False).hexdigest()
            assert [found.id for found in await drive.find_clip(clip_id)] == [remote.id]
        finally:
            await drive.trash(remote.id)
