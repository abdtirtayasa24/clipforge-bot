import asyncio
import logging
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import ProviderConfigError, ProviderRegistry
from clipforge.ai.types import AnalysisContext, ClipCandidate, TranscriptResult
from clipforge.ai.usage import usage_recorder
from clipforge.ai.windowing import Analyzer, ProviderChoice, excerpt
from clipforge.db.models import ClipCandidate as ClipCandidateRow
from clipforge.db.models import MediaAsset, Video
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import StageResult, write_artifact
from clipforge.pipeline.stages.frames import ensure_frames
from clipforge.pipeline.stages.transcribe import TranscriptArtifact, read_transcript
from clipforge.queue.errors import PermanentError
from clipforge.scoring.score import score
from clipforge.settings_schema import JobSettings, ModelRole

logger = logging.getLogger(__name__)

ANALYSIS_NAME = "analysis.json"
EXCERPT_MAX_CHARS = 2000
MIN_SHORTLIST = 6


class AnalysisArtifact(BaseModel):
    transcript_sha256s: list[str]
    candidates: list[ClipCandidate]
    window_models: list[str]
    rerank_model: str | None
    frames_sent: int


def read_analysis(path: Path) -> AnalysisArtifact | None:
    try:
        return AnalysisArtifact.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


def transcript_excerpt(transcript: TranscriptResult, candidate: ClipCandidate) -> str:
    return " ".join(segment.text for segment in excerpt(transcript, candidate))[:EXCERPT_MAX_CHARS]


def candidate_row(
    job_id: object, candidate: ClipCandidate, text: str, language: str | None = None
) -> dict[str, object]:
    scores = candidate.scores
    return {
        "job_id": job_id,
        "start_ms": round(candidate.start_s * 1000),
        "end_ms": round(candidate.end_s * 1000),
        "hook_score": scores.hook,
        "emotion_score": scores.emotion,
        "clarity_score": scores.clarity,
        "relevance_score": scores.relevance,
        "visual_score": scores.visual,
        "completeness_score": scores.completeness,
        "total_score": score(scores),
        "title": candidate.title,
        "hook_line": candidate.hook_line,
        "explanation": candidate.explanation,
        "transcript_excerpt": text,
        "topic": candidate.topic or None,
        "keywords": candidate.keywords,
        "language": language,
    }


class AnalyzeStage:
    """Windowed AI analysis plus frame re-rank; stores every candidate unselected (ADR-011)."""

    name: str = "analyzed"
    running_name: str | None = "analyzing"

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        quota: QuotaGuard,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._quota = quota

    def _chain(self, settings: JobSettings, roles: tuple[ModelRole, ...]) -> list[ProviderChoice]:
        """Providers in fallback order; unbuildable fallbacks are skipped, the primary is not."""
        chain: list[ProviderChoice] = []
        for position, role in enumerate(roles):
            snapshot = settings.providers.get(role)
            if snapshot is None or any(c.snapshot == snapshot for c in chain):
                continue
            try:
                chain.append(ProviderChoice(snapshot, self._registry.analysis(snapshot)))
            except ProviderConfigError as exc:
                if position == 0:
                    raise
                logger.warning(
                    "fallback provider skipped", extra={"role": role, "reason": str(exc)}
                )
        if not chain:
            raise PermanentError(f"no {roles[0]} provider in the job's settings snapshot")
        return chain

    async def run(self, ctx: StageContext) -> StageResult:
        artifact = await asyncio.to_thread(read_transcript, ctx.workspace)
        if artifact is None:
            raise PermanentError("the transcript is missing; transcription must run first")
        path = ctx.workspace / ANALYSIS_NAME
        analysis = await asyncio.to_thread(read_analysis, path)
        if analysis is None or analysis.transcript_sha256s != artifact.chunk_sha256s:
            analysis = await self._analyze(ctx, artifact)
            await write_artifact(path, analysis.model_dump_json())
        async with self._sessions.begin() as session:
            await session.execute(
                delete(ClipCandidateRow).where(ClipCandidateRow.job_id == ctx.job.lease.job_id)
            )
            if analysis.candidates:
                await session.execute(
                    insert(ClipCandidateRow),
                    [
                        candidate_row(
                            ctx.job.lease.job_id,
                            candidate,
                            transcript_excerpt(artifact.transcript, candidate),
                            artifact.transcript.language,
                        )
                        for candidate in analysis.candidates
                    ],
                )
        return StageResult(
            details={
                "candidates": len(analysis.candidates),
                "window_models": sorted(set(analysis.window_models)),
                "rerank_model": analysis.rerank_model,
                "frames_sent": analysis.frames_sent,
            }
        )

    async def _analyze(self, ctx: StageContext, artifact: TranscriptArtifact) -> AnalysisArtifact:
        transcript = artifact.transcript
        settings = JobSettings.of(ctx.job.settings_snapshot)
        window_chain = self._chain(settings, ("analysis", "analysis_fallback"))
        rerank_chain = self._chain(settings, ("rerank", "analysis", "analysis_fallback"))
        async with self._sessions() as session:
            video = await session.get_one(Video, ctx.job.video_id)
            asset = (
                await session.get(MediaAsset, ctx.job.media_asset_id)
                if ctx.job.media_asset_id
                else None
            )
        if asset is None or not asset.local_path or not asset.sha256:
            raise PermanentError("the validated source is missing")
        await ctx.check_cancelled()
        frames = await ensure_frames(ctx.workspace, Path(asset.local_path), asset.sha256)
        context = AnalysisContext(
            video_title=video.title,
            channel_title=video.channel_title,
            language=transcript.language,
            duration_s=artifact.duration_s,
            clip_min_s=settings.clip_min_s,
            clip_max_s=settings.clip_max_s,
        )
        analyzer = Analyzer(
            self._quota,
            lambda provider: usage_recorder(
                self._sessions, provider=provider, job_id=ctx.job.lease.job_id, stage=self.name
            ),
        )
        await ctx.check_cancelled()
        outcome = await analyzer.analyze(
            transcript,
            context,
            frames,
            window_chain=window_chain,
            rerank_chain=rerank_chain,
            shortlist_size=max(MIN_SHORTLIST, 2 * settings.max_clips),
        )
        reranked = {(c.start_s, c.end_s) for c in outcome.shortlist}
        others = [c for c in outcome.proposals if (c.start_s, c.end_s) not in reranked]
        return AnalysisArtifact(
            transcript_sha256s=artifact.chunk_sha256s,
            candidates=[*outcome.shortlist, *others],
            window_models=outcome.window_models,
            rerank_model=outcome.rerank_model,
            frames_sent=outcome.frames_sent,
        )
