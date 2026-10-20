import os
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import insert, update

from clipforge.db.models import Clip, ClipArtifact, ClipCandidate, MediaAsset, ProcessingJob
from clipforge.pipeline.cleanup import WorkspaceCleaner, job_workspace
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.runner import PipelineRunner
from clipforge.pipeline.stage import StageResult
from clipforge.queue.repository import JobQueue
from tests.clock import FakeClock
from tests.jobs import Sessions, claim, load_job, make_queued_job


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def age(path: Path, hours: float) -> None:
    stamp = path.stat().st_mtime - hours * 3600
    os.utime(path, (stamp, stamp))


def stage_files(workspace: Path) -> dict[str, Path]:
    return {
        "audio": touch(workspace / "audio" / "chunk_000.mp3"),
        "frames": touch(workspace / "frames" / "frame_00001.jpg"),
        "transcript": touch(workspace / "transcript" / "transcript.json"),
        "analysis": touch(workspace / "analysis.json"),
        "subtitles": touch(workspace / "clips" / "a.ass"),
    }


async def set_job(sessions: Sessions, job_id: uuid.UUID, **values: object) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(ProcessingJob).where(ProcessingJob.id == job_id).values(**values)
        )


async def unverified_clip(sessions: Sessions, job_id: uuid.UUID, path: Path) -> None:
    async with sessions.begin() as session:
        job = await session.get_one(ProcessingJob, job_id)
        candidate_id = await session.scalar(
            insert(ClipCandidate)
            .values(
                job_id=job_id,
                start_ms=0,
                end_ms=30_000,
                hook_score=90,
                emotion_score=90,
                clarity_score=90,
                relevance_score=90,
                visual_score=90,
                completeness_score=90,
                total_score=90,
            )
            .returning(ClipCandidate.id)
        )
        clip_id = await session.scalar(
            insert(Clip)
            .values(
                candidate_id=candidate_id,
                job_id=job_id,
                video_id=job.video_id,
                start_ms=0,
                end_ms=30_000,
            )
            .returning(Clip.id)
        )
        await session.execute(
            insert(ClipArtifact).values(clip_id=clip_id, kind="video", local_path=str(path))
        )


async def test_completion_keeps_only_small_artifacts_and_unuploaded_clips(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    workspace = job_workspace(tmp_path, job_id)
    files = stage_files(workspace)
    clip = touch(workspace / "clips" / "kept.mp4")
    await unverified_clip(sessions, job_id, clip)
    job = await load_job(sessions, job_id)
    source = touch(tmp_path / "sources" / str(job.media_asset_id))
    async with sessions.begin() as session:
        await session.execute(
            update(MediaAsset)
            .where(MediaAsset.id == job.media_asset_id)
            .values(local_path=str(source))
        )

    await WorkspaceCleaner(sessions, tmp_path).after_completion(job_id)

    assert not files["audio"].exists() and not files["frames"].exists()
    assert not files["subtitles"].exists()
    assert files["transcript"].exists() and files["analysis"].exists()
    assert clip.exists()
    assert not source.exists()
    async with sessions() as session:
        asset = await session.get_one(MediaAsset, job.media_asset_id)
    assert asset.state == "deleted"


async def test_gc_removes_only_old_terminal_or_unknown_job_dirs(
    queue: JobQueue, sessions: Sessions, clock: FakeClock, tmp_path: Path
) -> None:
    old = clock.now - timedelta(hours=48)
    recent = clock.now - timedelta(hours=1)
    old_done = await make_queued_job(queue, sessions)
    await set_job(sessions, old_done, status="completed", stage="completed", finished_at=old)
    recent_failed = await make_queued_job(queue, sessions)
    await set_job(sessions, recent_failed, status="failed", finished_at=recent)
    old_queued = await make_queued_job(queue, sessions)
    old_failed_with_clip = await make_queued_job(queue, sessions)
    await set_job(sessions, old_failed_with_clip, status="failed", finished_at=old)
    for job_id in (old_done, recent_failed, old_queued, old_failed_with_clip):
        stage_files(job_workspace(tmp_path, job_id))
    clip = touch(job_workspace(tmp_path, old_failed_with_clip) / "clips" / "pending.mp4")
    await unverified_clip(sessions, old_failed_with_clip, clip)
    unknown = job_workspace(tmp_path, uuid.uuid4())
    touch(unknown / "audio" / "chunk_000.mp3")
    for path in [*unknown.rglob("*"), unknown]:
        age(path, 48)
    stray = touch(tmp_path / "jobs" / "not-a-job" / "file")

    removed = await WorkspaceCleaner(sessions, tmp_path, clock=clock).collect_garbage(
        older_than=timedelta(hours=24)
    )

    assert not job_workspace(tmp_path, old_done).exists()
    assert not unknown.exists()
    assert job_workspace(tmp_path, recent_failed).exists()
    assert job_workspace(tmp_path, old_queued).exists()
    assert clip.exists(), "an un-uploaded clip is never deleted"
    assert not (job_workspace(tmp_path, old_failed_with_clip) / "audio").exists()
    assert stray.exists()
    assert set(removed) == {
        job_workspace(tmp_path, old_done),
        unknown,
        job_workspace(tmp_path, old_failed_with_clip),
    }


class WritesStageFiles:
    name = "validated"
    running_name: str | None = None

    async def run(self, ctx: StageContext) -> StageResult:
        stage_files(ctx.workspace)
        return StageResult()


async def test_runner_cleans_the_workspace_after_completion(
    queue: JobQueue, sessions: Sessions, tmp_path: Path
) -> None:
    job_id = await make_queued_job(queue, sessions)
    runner = PipelineRunner(
        queue,
        [WritesStageFiles()],
        workspace_root=tmp_path,
        cleaner=WorkspaceCleaner(sessions, tmp_path),
    )

    assert await runner.run(await claim(queue)) == "completed"

    workspace = job_workspace(tmp_path, job_id)
    assert not (workspace / "audio").exists()
    assert (workspace / "transcript" / "transcript.json").exists()
