import asyncio
import logging
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.ai.adapters.gemini_client import ModelUnavailable
from clipforge.ai.protocols import TranscriptionProvider
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import ProviderRegistry
from clipforge.ai.timestamps import ChunkTranscript, merge_chunks
from clipforge.ai.types import TranscriptResult
from clipforge.ai.usage import usage_recorder
from clipforge.db.models import Video
from clipforge.media.audio import AudioChunk
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import StageResult, write_artifact
from clipforge.pipeline.stages.audio import AUDIO_DIR, manifest_path, read_manifest
from clipforge.queue.errors import PermanentError
from clipforge.settings_schema import JobSettings, ProviderSnapshot

logger = logging.getLogger(__name__)

TRANSCRIPT_DIR = "transcript"
TRANSCRIPT_NAME = "transcript.json"
LANGUAGE_HINTS = {"id": "id-ID", "en": "en-US"}


class ChunkResult(BaseModel):
    chunk_sha256: str
    model: str
    transcript: TranscriptResult


class TranscriptArtifact(BaseModel):
    source_sha256: str
    chunk_sha256s: list[str]
    duration_s: float
    transcript: TranscriptResult


def transcript_path(workspace: Path) -> Path:
    return workspace / TRANSCRIPT_DIR / TRANSCRIPT_NAME


def read_transcript(workspace: Path) -> TranscriptArtifact | None:
    try:
        return TranscriptArtifact.model_validate_json(transcript_path(workspace).read_bytes())
    except (OSError, ValidationError):
        return None


def _read_chunk_result(path: Path, chunk: AudioChunk) -> ChunkResult | None:
    try:
        result = ChunkResult.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None
    return result if result.chunk_sha256 == chunk.sha256 else None


def language_hint(default_language: str | None) -> str | None:
    if not default_language:
        return None
    if "-" in default_language:
        return default_language
    return LANGUAGE_HINTS.get(default_language.lower())


def _fallback_provider(settings: JobSettings) -> ProviderSnapshot | None:
    """The analysis model doubles as a segment-level transcriber (ADR-027)."""
    analysis = settings.providers.get("analysis")
    if analysis is None or analysis.provider != "gemini":
        return None
    return analysis.model_copy(update={"capability": "transcription"})


class TranscribeStage:
    """Transcribes audio chunks one at a time, keeping each result so a restart resumes."""

    name: str = "transcribed"
    running_name: str | None = None

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        quota: QuotaGuard,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._quota = quota

    async def run(self, ctx: StageContext) -> StageResult:
        manifest = await asyncio.to_thread(read_manifest, manifest_path(ctx.workspace))
        if manifest is None:
            raise PermanentError("the audio manifest is missing; audio must be extracted first")
        chunk_hashes = [chunk.sha256 for chunk in manifest.chunks]
        existing = await asyncio.to_thread(read_transcript, ctx.workspace)
        if existing and (existing.source_sha256, existing.chunk_sha256s) == (
            manifest.source_sha256,
            chunk_hashes,
        ):
            return StageResult(details=self._details(existing.transcript, called=0, fallback=0))

        out_dir = ctx.workspace / TRANSCRIPT_DIR
        await asyncio.to_thread(out_dir.mkdir, mode=0o700, exist_ok=True)
        results: dict[int, ChunkResult] = {}
        for chunk in manifest.chunks:
            path = out_dir / f"chunk_{chunk.index:03d}.json"
            cached = await asyncio.to_thread(_read_chunk_result, path, chunk)
            if cached:
                results[chunk.index] = cached
        pending = [chunk for chunk in manifest.chunks if chunk.index not in results]

        settings = JobSettings.of(ctx.job.settings_snapshot)
        fallback_used = 0
        if pending:
            primary = settings.provider("transcription")
            await self._quota.reserve(primary, len(pending))
            async with self._sessions() as session:
                video = await session.get_one(Video, ctx.job.video_id)
            hint = language_hint(video.default_language)
            for chunk in pending:
                await ctx.check_cancelled()
                result, used_fallback = await self._transcribe(ctx, settings, primary, chunk, hint)
                fallback_used += used_fallback
                path = out_dir / f"chunk_{chunk.index:03d}.json"
                await write_artifact(path, result.model_dump_json())
                results[chunk.index] = result

        transcript = merge_chunks(
            [
                ChunkTranscript(chunk.start_s, results[chunk.index].transcript)
                for chunk in manifest.chunks
            ],
            overlap_s=manifest.overlap_s,
            duration_s=manifest.duration_s,
        )
        artifact = TranscriptArtifact(
            source_sha256=manifest.source_sha256,
            chunk_sha256s=chunk_hashes,
            duration_s=manifest.duration_s,
            transcript=transcript,
        )
        await write_artifact(transcript_path(ctx.workspace), artifact.model_dump_json())
        return StageResult(
            details=self._details(transcript, called=len(pending), fallback=fallback_used)
        )

    async def _transcribe(
        self,
        ctx: StageContext,
        settings: JobSettings,
        primary: ProviderSnapshot,
        chunk: AudioChunk,
        hint: str | None,
    ) -> tuple[ChunkResult, bool]:
        audio = ctx.workspace / AUDIO_DIR / chunk.file
        try:
            transcript = await self._call(
                self._registry.transcription(primary), primary, ctx, audio, chunk, hint
            )
            return ChunkResult(
                chunk_sha256=chunk.sha256, model=primary.model, transcript=transcript
            ), False
        except ModelUnavailable as exc:
            fallback = _fallback_provider(settings)
            if fallback is None:
                raise
            logger.warning(
                "transcription model unavailable; using fallback",
                extra={"chunk": chunk.index, "model": fallback.model, "reason": str(exc)},
            )
        await self._quota.reserve(fallback, 1)
        transcript = await self._call(
            self._registry.transcription_fallback(fallback), fallback, ctx, audio, chunk, hint
        )
        return ChunkResult(
            chunk_sha256=chunk.sha256, model=fallback.model, transcript=transcript
        ), True

    async def _call(
        self,
        provider: TranscriptionProvider,
        snapshot: ProviderSnapshot,
        ctx: StageContext,
        audio: Path,
        chunk: AudioChunk,
        hint: str | None,
    ) -> TranscriptResult:
        record = usage_recorder(
            self._sessions, provider=snapshot, job_id=ctx.job.lease.job_id, stage=self.name
        )
        return await provider.transcribe(
            audio,
            duration_s=chunk.end_s - chunk.start_s,
            language_hint=hint,
            record=record,
        )

    @staticmethod
    def _details(transcript: TranscriptResult, *, called: int, fallback: int) -> dict[str, object]:
        return {
            "language": transcript.language,
            "segments": len(transcript.segments),
            "words": len(transcript.words),
            "chunks_transcribed": called,
            "fallback_chunks": fallback,
        }
