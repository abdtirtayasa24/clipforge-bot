import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, insert, select, update

from clipforge.ai.adapters.fake import spoken_transcript
from clipforge.ai.registry import snapshot_job_settings
from clipforge.checksums import file_md5, file_sha256
from clipforge.db.models import Clip, ClipArtifact, ClipCandidate, MediaAsset, ProcessingJob
from clipforge.media.probe import probe
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stages import render as render_module
from clipforge.pipeline.stages.render import CLIPS_DIR, RenderStage
from clipforge.pipeline.stages.transcribe import TranscriptArtifact, transcript_path
from clipforge.queue.repository import JobQueue
from tests.clock import FakeClock
from tests.jobs import Sessions, claim, make_queued_job

pytestmark = pytest.mark.usefixtures("require_ffmpeg")

SELECTED = [(1, 100_000, 125_000, "Satu"), (2, 700_000, 722_500, "Dua")]


def candidate_values(
    job_id: uuid.UUID, start_ms: int, end_ms: int, title: str
) -> dict[str, object]:
    return {
        "job_id": job_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "hook_score": 90,
        "emotion_score": 90,
        "clarity_score": 90,
        "relevance_score": 90,
        "visual_score": 90,
        "completeness_score": 90,
        "total_score": 90,
        "title": title,
        "hook_line": f"Pembuka {title}",
    }


@pytest.fixture
async def ctx(
    sessions: Sessions, clock: FakeClock, tmp_path: Path, valid_source: Path
) -> StageContext:
    queue = JobQueue(
        sessions, clock=clock, jitter=lambda: 0.0, snapshot_settings=snapshot_job_settings
    )
    job_id = await make_queued_job(queue, sessions)
    async with sessions.begin() as session:
        job = await session.get_one(ProcessingJob, job_id)
        job.stage = "candidates_selected"
        await session.execute(
            update(MediaAsset)
            .where(MediaAsset.id == job.media_asset_id)
            .values(local_path=str(valid_source), sha256=file_sha256(valid_source))
        )
        for rank, start_ms, end_ms, title in SELECTED:
            await session.execute(
                insert(ClipCandidate).values(
                    **candidate_values(job_id, start_ms, end_ms, title), selected=True, rank=rank
                )
            )
        await session.execute(
            insert(ClipCandidate).values(**candidate_values(job_id, 300_000, 330_000, "Lemah"))
        )
    transcript_path(tmp_path).parent.mkdir(parents=True)
    artifact = TranscriptArtifact(
        source_sha256="a" * 64,
        chunk_sha256s=["b" * 64],
        duration_s=31 * 60,
        transcript=spoken_transcript(31 * 60),
    )
    transcript_path(tmp_path).write_text(artifact.model_dump_json())
    return StageContext(job=await claim(queue), queue=queue, workspace=tmp_path)


async def clips_of(sessions: Sessions, job_id: uuid.UUID) -> list[tuple[Clip, ClipArtifact]]:
    async with sessions() as session:
        rows = await session.execute(
            select(Clip, ClipArtifact)
            .join(ClipArtifact, ClipArtifact.clip_id == Clip.id)
            .where(Clip.job_id == job_id, ClipArtifact.kind == "video")
            .order_by(Clip.start_ms)
        )
        return [(clip, artifact) for clip, artifact in rows]


def count_renders(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    rendered: list[Path] = []
    real = render_module.render_clip

    async def counting(source: Path, dest: Path, *args: object, **kwargs: object) -> object:
        rendered.append(dest)
        return await real(source, dest, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(render_module, "render_clip", counting)
    return rendered


async def test_selected_candidates_render_compliant_clips(
    sessions: Sessions, ctx: StageContext
) -> None:
    result = await RenderStage(sessions).run(ctx)

    clips = await clips_of(sessions, ctx.job.lease.job_id)
    assert [clip.title for clip, _ in clips] == ["Satu", "Dua"]
    assert result.details == {"clips": 2, "rendered": 2, "reused": 0}
    for (clip, artifact), (_, start_ms, end_ms, _) in zip(clips, SELECTED, strict=True):
        assert (clip.start_ms, clip.end_ms, clip.video_id) == (start_ms, end_ms, ctx.job.video_id)
        path = Path(artifact.local_path or "")
        assert path == ctx.workspace / CLIPS_DIR / f"{clip.id}.mp4"
        assert artifact.upload_state == "pending"
        assert artifact.size_bytes == path.stat().st_size
        assert artifact.sha256 == file_sha256(path)
        assert artifact.md5 == file_md5(path)
        media = await probe(path)
        assert (media.width, media.height) == (720, 1280)
        assert (media.video_codec, media.audio_codec) == ("h264", "aac")
        assert media.duration_s is not None
        assert abs(media.duration_s - (end_ms - start_ms) / 1000) <= 0.5
        assert 20 <= media.duration_s <= 60
    assert not list((ctx.workspace / CLIPS_DIR).glob("*.part.mp4"))


@pytest.mark.parametrize("enabled", [True, False])
async def test_title_overlay_follows_the_job_setting(
    sessions: Sessions, ctx: StageContext, enabled: bool
) -> None:
    snapshot = {**ctx.job.settings_snapshot, "title_overlay_enabled": enabled}
    ctx = replace(ctx, job=replace(ctx.job, settings_snapshot=snapshot))

    await RenderStage(sessions).run(ctx)

    burned = [
        [
            line.rsplit(",", 1)[-1]
            for line in (ctx.workspace / CLIPS_DIR / f"{clip.id}.ass")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.startswith("Dialogue: 1,")
        ]
        for clip, _ in await clips_of(sessions, ctx.job.lease.job_id)
    ]
    assert burned == ([["Satu"], ["Dua"]] if enabled else [[], []])


@pytest.mark.parametrize("enabled", [True, False])
async def test_subject_reframing_follows_the_job_setting(
    sessions: Sessions, ctx: StageContext, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    snapshot = {**ctx.job.settings_snapshot, "subject_reframing_enabled": enabled}
    ctx = replace(ctx, job=replace(ctx.job, settings_snapshot=snapshot))
    planned: list[float] = []
    real = render_module.plan_reframing

    async def planning(source: Path, work_dir: Path, start_s: float, end_s: float) -> object:
        planned.append(start_s)
        return await real(source, work_dir, start_s, end_s)

    monkeypatch.setattr(render_module, "plan_reframing", planning)

    await RenderStage(sessions).run(ctx)

    assert planned == ([100.0, 700.0] if enabled else [])
    for _, artifact in await clips_of(sessions, ctx.job.lease.job_id):
        media = await probe(Path(artifact.local_path or ""))
        assert (media.width, media.height) == (720, 1280)
    assert not list((ctx.workspace / CLIPS_DIR).glob("*.reframe"))


async def test_restart_re_renders_only_unfinished_clips(
    sessions: Sessions, ctx: StageContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = RenderStage(sessions)
    await stage.run(ctx)
    (first, first_artifact), (second, second_artifact) = await clips_of(
        sessions, ctx.job.lease.job_id
    )
    first_path = Path(first_artifact.local_path or "")
    first_mtime = first_path.stat().st_mtime_ns
    # A crash while the second clip was encoding: a partial file and no artifact row.
    second_path = Path(second_artifact.local_path or "")
    async with sessions.begin() as session:
        await session.execute(delete(ClipArtifact).where(ClipArtifact.id == second_artifact.id))
    second_path.unlink()
    second_path.with_name(f"{second.id}.part.mp4").write_bytes(b"half")
    rendered = count_renders(monkeypatch)

    result = await stage.run(ctx)

    assert [path.name for path in rendered] == [f"{second.id}.part.mp4"]
    assert result.details == {"clips": 2, "rendered": 1, "reused": 1}
    assert first_path.stat().st_mtime_ns == first_mtime
    clips = await clips_of(sessions, ctx.job.lease.job_id)
    assert [clip.id for clip, _ in clips] == [first.id, second.id]
    assert clips[1][1].sha256 == file_sha256(second_path)


async def test_tampered_clip_is_re_rendered_and_uploaded_clip_is_kept(
    sessions: Sessions, ctx: StageContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = RenderStage(sessions)
    await stage.run(ctx)
    (first, first_artifact), (_, second_artifact) = await clips_of(sessions, ctx.job.lease.job_id)
    Path(first_artifact.local_path or "").write_bytes(b"corrupted")
    async with sessions.begin() as session:
        await session.execute(
            update(ClipArtifact)
            .where(ClipArtifact.id == second_artifact.id)
            .values(
                upload_state="verified",
                drive_file_id="drive-file",
                local_deleted_at=datetime.now(UTC),
            )
        )
    Path(second_artifact.local_path or "").unlink()
    rendered = count_renders(monkeypatch)

    await stage.run(ctx)

    assert [path.name for path in rendered] == [f"{first.id}.part.mp4"]
    clips = await clips_of(sessions, ctx.job.lease.job_id)
    assert clips[0][1].sha256 == file_sha256(Path(first_artifact.local_path or ""))
    assert clips[1][1].upload_state == "verified"
    assert not Path(second_artifact.local_path or "").exists()
