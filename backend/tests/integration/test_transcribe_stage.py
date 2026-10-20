import hashlib
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update

from clipforge.ai.adapters.fake import FakeTranscriptionProvider, spoken_transcript
from clipforge.ai.adapters.gemini_client import ModelUnavailable
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import ProviderRegistry, snapshot_job_settings
from clipforge.ai.types import TranscriptResult
from clipforge.ai.usage import ProviderCall, UsageRecorder
from clipforge.db.models import AiProviderConfig, ProcessingJob, ProviderUsage
from clipforge.media.audio import AudioChunk, AudioManifest
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stages.audio import AUDIO_DIR, manifest_path
from clipforge.pipeline.stages.transcribe import TranscribeStage, read_transcript
from clipforge.queue.errors import PermanentError, QuotaDeferred, TransientError
from clipforge.queue.repository import JobQueue
from clipforge.settings_schema import ProviderSnapshot
from tests.clock import FakeClock
from tests.jobs import Sessions, claim, make_queued_job

TRANSCRIBE = "gemini-3.5-transcribe"
CHUNKS = [(0.0, 602.0), (600.0, 1202.0), (1200.0, 1500.0)]


class ScriptedProvider:
    """Fake transcription whose calls can fail on chosen chunk files."""

    def __init__(self, fail_on: dict[str, Exception] | None = None) -> None:
        self.fake = FakeTranscriptionProvider()
        self.fail_on = dict(fail_on or {})
        self.calls: list[str] = []

    async def transcribe(
        self,
        audio_path: Path,
        *,
        duration_s: float,
        language_hint: str | None,
        record: UsageRecorder,
    ) -> TranscriptResult:
        self.calls.append(audio_path.name)
        error = self.fail_on.pop(audio_path.name, None)
        if error is not None:
            await record(ProviderCall(success=False, latency_ms=3, error_code="503"))
            raise error
        return await self.fake.transcribe(
            audio_path, duration_s=duration_s, language_hint=language_hint, record=record
        )


def write_manifest(workspace: Path) -> AudioManifest:
    audio_dir = workspace / AUDIO_DIR
    audio_dir.mkdir(parents=True)
    chunks = []
    for index, (start, end) in enumerate(CHUNKS):
        data = f"chunk-{index}".encode()
        (audio_dir / f"chunk_{index:03d}.mp3").write_bytes(data)
        chunks.append(
            AudioChunk(
                index=index,
                file=f"chunk_{index:03d}.mp3",
                start_s=start,
                end_s=end,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
            )
        )
    manifest = AudioManifest(
        source_sha256="a" * 64, duration_s=1500, chunk_s=600, overlap_s=2, chunks=chunks
    )
    manifest_path(workspace).write_text(manifest.model_dump_json())
    return manifest


@pytest.fixture
async def priced_transcription(sessions: Sessions) -> AsyncIterator[None]:
    async with sessions.begin() as session:
        await session.execute(
            update(AiProviderConfig)
            .where(AiProviderConfig.model == TRANSCRIBE)
            .values(price_per_audio_minute=Decimal("0.006"))
        )
    yield
    async with sessions.begin() as session:
        await session.execute(
            update(AiProviderConfig)
            .where(AiProviderConfig.model == TRANSCRIBE)
            .values(price_per_audio_minute=0)
        )


@pytest.fixture
async def ctx(sessions: Sessions, clock: FakeClock, tmp_path: Path) -> StageContext:
    queue = JobQueue(
        sessions, clock=clock, jitter=lambda: 0.0, snapshot_settings=snapshot_job_settings
    )
    job_id = await make_queued_job(queue, sessions)
    async with sessions.begin() as session:
        await session.execute(
            update(ProcessingJob).where(ProcessingJob.id == job_id).values(stage="audio_extracted")
        )
    write_manifest(tmp_path)
    return StageContext(job=await claim(queue), queue=queue, workspace=tmp_path)


def stage_for(
    sessions: Sessions,
    primary: ScriptedProvider,
    fallback: ScriptedProvider | None = None,
) -> TranscribeStage:
    def build(provider: ScriptedProvider) -> Callable[[ProviderSnapshot, str], ScriptedProvider]:
        return lambda snapshot, key: provider

    registry = ProviderRegistry(
        transcription={"gemini": build(primary)},
        transcription_fallback={"gemini": build(fallback or ScriptedProvider())},
        environ={"GEMINI_API_KEY": "k"},
    )
    return TranscribeStage(sessions, registry, QuotaGuard(sessions))


async def usage_rows(sessions: Sessions, ctx: StageContext) -> list[ProviderUsage]:
    async with sessions() as session:
        rows = await session.scalars(
            select(ProviderUsage)
            .where(ProviderUsage.job_id == ctx.job.lease.job_id)
            .order_by(ProviderUsage.created_at, ProviderUsage.id)
        )
        return list(rows)


@pytest.mark.usefixtures("priced_transcription")
async def test_chunks_merge_into_one_absolute_transcript(
    sessions: Sessions, ctx: StageContext
) -> None:
    primary = ScriptedProvider()

    result = await stage_for(sessions, primary).run(ctx)

    artifact = read_transcript(ctx.workspace)
    assert artifact is not None
    words = artifact.transcript.words
    assert primary.calls == ["chunk_000.mp3", "chunk_001.mp3", "chunk_002.mp3"]
    assert [w.start_s for w in words] == sorted(w.start_s for w in words)
    assert all(0 <= w.start_s <= w.end_s <= 1500 for w in words)
    assert words[-1].start_s > 1400
    assert result.details["chunks_transcribed"] == 3
    rows = await usage_rows(sessions, ctx)
    assert [(r.model, r.stage, r.audio_seconds) for r in rows] == [
        (TRANSCRIBE, "transcribed", Decimal("602.000")),
        (TRANSCRIBE, "transcribed", Decimal("602.000")),
        (TRANSCRIBE, "transcribed", Decimal("300.000")),
    ]
    assert [r.estimated_cost for r in rows] == [
        Decimal("0.0602"),
        Decimal("0.0602"),
        Decimal("0.03"),
    ]


async def test_restart_after_two_chunks_only_calls_the_third(
    sessions: Sessions, ctx: StageContext
) -> None:
    failing = ScriptedProvider(fail_on={"chunk_002.mp3": TransientError("timeout")})
    with pytest.raises(TransientError):
        await stage_for(sessions, failing).run(ctx)

    resumed = ScriptedProvider()
    result = await stage_for(sessions, resumed).run(ctx)

    assert failing.calls == ["chunk_000.mp3", "chunk_001.mp3", "chunk_002.mp3"]
    assert resumed.calls == ["chunk_002.mp3"]
    assert result.details["chunks_transcribed"] == 1
    rows = await usage_rows(sessions, ctx)
    assert [r.success for r in rows] == [True, True, False, True]


async def test_completed_transcript_is_reused(sessions: Sessions, ctx: StageContext) -> None:
    await stage_for(sessions, ScriptedProvider()).run(ctx)
    again = ScriptedProvider()

    result = await stage_for(sessions, again).run(ctx)

    assert again.calls == []
    assert result.details["chunks_transcribed"] == 0


async def test_unavailable_model_falls_back_to_segment_transcription(
    sessions: Sessions, ctx: StageContext
) -> None:
    primary = ScriptedProvider(fail_on={"chunk_001.mp3": ModelUnavailable("404")})
    fallback = ScriptedProvider()

    result = await stage_for(sessions, primary, fallback).run(ctx)

    assert fallback.calls == ["chunk_001.mp3"]
    assert result.details["fallback_chunks"] == 1
    rows = await usage_rows(sessions, ctx)
    assert [(r.model, r.capability, r.success) for r in rows] == [
        (TRANSCRIBE, "transcription", True),
        (TRANSCRIBE, "transcription", False),
        ("gemini-3.5-flash-lite", "transcription", True),
        (TRANSCRIBE, "transcription", True),
    ]


async def test_exhausted_daily_quota_defers_before_any_call(
    sessions: Sessions, ctx: StageContext
) -> None:
    provider = ProviderSnapshot.model_validate(
        ctx.job.settings_snapshot["providers"]["transcription"]
    )
    async with sessions.begin() as session:
        session.add_all(
            ProviderUsage(
                provider="gemini",
                model=provider.model,
                capability="transcription",
                success=True,
                latency_ms=1,
            )
            for _ in range(provider.rpd_limit or 0)
        )
    primary = ScriptedProvider()

    try:
        with pytest.raises(QuotaDeferred):
            await stage_for(sessions, primary).run(ctx)
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderUsage).where(
                    ProviderUsage.model == provider.model, ProviderUsage.job_id.is_(None)
                )
            )

    assert primary.calls == []


async def test_transcript_without_audio_manifest_fails_permanently(
    sessions: Sessions, ctx: StageContext
) -> None:
    manifest_path(ctx.workspace).unlink()

    with pytest.raises(PermanentError, match="manifest"):
        await stage_for(sessions, ScriptedProvider()).run(ctx)


def test_fake_transcript_covers_its_duration() -> None:
    transcript = spoken_transcript(602)

    assert transcript.segments[-1].end_s <= 602
    assert transcript.segments[-1].end_s > 595
