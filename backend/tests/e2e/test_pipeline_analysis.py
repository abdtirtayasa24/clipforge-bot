"""The pipeline from ingest to Drive upload with fake AI providers and a fake Drive."""

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import delete, insert, select

from clipforge.ai.adapters.fake import FakeAnalysisProvider, FakeTranscriptionProvider
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import ProviderRegistry, snapshot_job_settings
from clipforge.ai.types import ClipCandidate, DimensionScores
from clipforge.checksums import file_sha256
from clipforge.db.models import AppSetting, Clip, ClipArtifact, MediaAsset, ProviderUsage, Video
from clipforge.db.models import ClipCandidate as ClipCandidateRow
from clipforge.media.probe import probe
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.registry import default_stages
from clipforge.pipeline.runner import PipelineRunner
from clipforge.pipeline.stage import StageResult
from clipforge.queue.repository import JobQueue
from clipforge.settings_schema import ProviderSnapshot
from clipforge.storage.drive import CLIP_ID_PROPERTY, DriveClient
from clipforge.storage.drive_oauth import FOLDER_KEY
from tests.clock import FakeClock
from tests.drive import FakeDrive, FakeTokens
from tests.jobs import Sessions, claim, load_job, stage_events

pytestmark = pytest.mark.usefixtures("require_ffmpeg")

PIPELINE_STAGES = [
    "ingesting",
    "validated",
    "extracting_audio",
    "audio_extracted",
    "transcribing",
    "transcribed",
    "analyzing",
    "analyzed",
    "selecting_candidates",
    "candidates_selected",
    "rendering_clips",
    "rendering",
    "uploading_clips",
    "uploading",
]


class NextStageSentinel:
    """Stands in for the stage after upload to show whether the pipeline reached it."""

    name = "affiliate_matching"
    running_name: str | None = None

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, ctx: StageContext) -> StageResult:
        self.calls += 1
        return StageResult()


def candidate(start: float, end: float, level: int, title: str) -> ClipCandidate:
    return ClipCandidate(
        start_s=start,
        end_s=end,
        scores=DimensionScores(
            hook=level,
            emotion=level,
            clarity=level,
            relevance=level,
            visual=level,
            completeness=level,
        ),
        title=title,
        hook_line=f"Pembuka {title}",
        explanation="uji",
        topic=f"Topik {title}",
        keywords=[title.lower()],
    )


def registry(analysis: FakeAnalysisProvider) -> ProviderRegistry:
    def fixed[T](provider: T) -> Callable[[ProviderSnapshot, str], T]:
        return lambda snapshot, key: provider

    return ProviderRegistry(
        transcription={"gemini": fixed(FakeTranscriptionProvider())},
        analysis={"gemini": fixed(analysis)},
        environ={"GEMINI_API_KEY": "test", "OPENROUTER_API_KEY": "test"},
    )


@pytest.fixture
def drive() -> Iterator[FakeDrive]:
    fake = FakeDrive()
    with respx.mock(base_url="https://www.googleapis.com", assert_all_called=False) as router:
        router.route().mock(side_effect=fake.handle)
        yield fake


async def run_pipeline(
    sessions: Sessions,
    clock: FakeClock,
    source: Path,
    workspace: Path,
    proposals: list[ClipCandidate],
) -> tuple[str, uuid.UUID, NextStageSentinel, FakeAnalysisProvider]:
    async with sessions.begin() as session:
        video_id = await session.scalar(
            insert(Video)
            .values(
                youtube_video_id=uuid.uuid4().hex[:11],
                title="Diskusi teknologi",
                channel_id="UCe2e",
                channel_title="Kanal E2E",
                default_language="id",
            )
            .returning(Video.id)
        )
        asset_id = await session.scalar(
            insert(MediaAsset)
            .values(
                video_id=video_id,
                kind="upload",
                permission_evidence={"type": "own_content"},
                state="ready",
                local_path=str(source),
                sha256=file_sha256(source),
                media_info=(await probe(source)).model_dump(mode="json"),
            )
            .returning(MediaAsset.id)
        )
    assert video_id is not None and asset_id is not None
    queue = JobQueue(
        sessions, clock=clock, jitter=lambda: 0.0, snapshot_settings=snapshot_job_settings
    )
    job, _ = await queue.enqueue(video_id, uuid.uuid4().hex, media_asset_id=asset_id)
    analysis = FakeAnalysisProvider(proposals)
    sentinel = NextStageSentinel()
    quota = QuotaGuard(sessions, clock=clock)
    async with sessions.begin() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key == FOLDER_KEY))
    async with httpx.AsyncClient() as http:
        stages = [
            *default_stages(
                sessions,
                workspace_root=workspace,
                registry=registry(analysis),
                quota=quota,
                drive=DriveClient(http, FakeTokens(), sessions),
            ),
            sentinel,
        ]
        runner = PipelineRunner(queue, stages, workspace_root=workspace)
        status = await runner.run(await claim(queue))
    return status, job.id, sentinel, analysis


async def candidate_rows(sessions: Sessions, job_id: uuid.UUID) -> list[ClipCandidateRow]:
    async with sessions() as session:
        rows = await session.scalars(
            select(ClipCandidateRow)
            .where(ClipCandidateRow.job_id == job_id)
            .order_by(ClipCandidateRow.rank.nulls_last(), ClipCandidateRow.start_ms)
        )
        return list(rows)


async def clip_rows(sessions: Sessions, job_id: uuid.UUID) -> list[tuple[Clip, ClipArtifact]]:
    async with sessions() as session:
        rows = await session.execute(
            select(Clip, ClipArtifact)
            .join(ClipArtifact, ClipArtifact.clip_id == Clip.id)
            .where(Clip.job_id == job_id)
        )
        return [(clip, artifact) for clip, artifact in rows]


async def test_job_uploads_at_most_three_verified_clips(
    sessions: Sessions, clock: FakeClock, valid_source: Path, tmp_path: Path, drive: FakeDrive
) -> None:
    proposals = [
        candidate(100, 130, 90, "Satu"),
        candidate(700, 745, 88, "Dua"),
        candidate(1300, 1340, 86, "Tiga"),
        candidate(1500, 1550, 84, "Empat"),
        candidate(300, 330, 40, "Lemah"),
    ]

    status, job_id, sentinel, analysis = await run_pipeline(
        sessions, clock, valid_source, tmp_path, proposals
    )

    assert status == "completed"
    assert (await stage_events(sessions, job_id))[: len(PIPELINE_STAGES)] == PIPELINE_STAGES
    assert sentinel.calls == 1
    rows = await candidate_rows(sessions, job_id)
    selected = [row for row in rows if row.selected]
    assert len(rows) == 5
    assert [(row.rank, row.title) for row in selected] == [(1, "Satu"), (2, "Dua"), (3, "Tiga")]
    for row in selected:
        assert 20_000 <= row.end_ms - row.start_ms <= 60_000
        assert row.total_score >= 75
        assert row.hook_line and row.transcript_excerpt
        assert (row.topic, row.keywords) == (f"Topik {row.title}", [str(row.title).lower()])
        assert row.language
    assert analysis.reranked_frames, "frames are sent for the shortlist"
    clips = await clip_rows(sessions, job_id)
    assert sorted(clip.title for clip, _ in clips) == ["Dua", "Satu", "Tiga"]
    for clip, artifact in clips:
        [remote] = drive.live_files(**{CLIP_ID_PROPERTY: str(clip.id)})
        assert (artifact.upload_state, clip.drive_file_id) == ("verified", remote["id"])
        assert remote["md5Checksum"] == artifact.md5
        assert artifact.local_deleted_at is not None
    async with sessions() as session:
        stages = set(
            await session.scalars(select(ProviderUsage.stage).where(ProviderUsage.job_id == job_id))
        )
    assert stages == {"transcribed", "analyzed"}
    costs = await JobQueue(sessions).estimated_costs(job_id)
    assert set(costs) == {"USD"}
    job = await load_job(sessions, job_id)
    assert job.settings_snapshot["providers"]["analysis"]["model"] == "gemini-3.5-flash-lite"


async def test_low_scores_complete_without_clips_or_rendering(
    sessions: Sessions, clock: FakeClock, valid_source: Path, tmp_path: Path, drive: FakeDrive
) -> None:
    proposals = [candidate(100, 130, 60, "Biasa"), candidate(700, 740, 74, "Hampir")]

    status, job_id, sentinel, _ = await run_pipeline(
        sessions, clock, valid_source, tmp_path, proposals
    )

    job = await load_job(sessions, job_id)
    assert status == "completed_no_clips"
    assert (job.status, job.stage) == ("completed_no_clips", "completed_no_clips")
    assert (await stage_events(sessions, job_id))[-1] == "candidates_selected"
    assert sentinel.calls == 0
    rows = await candidate_rows(sessions, job_id)
    assert len(rows) == 2
    assert not any(row.selected for row in rows)
    assert await clip_rows(sessions, job_id) == []
    assert drive.requests == []
