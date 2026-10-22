import asyncio

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.ai.types import ClipCandidate, DimensionScores
from clipforge.db.models import ClipCandidate as ClipCandidateRow
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import StageResult
from clipforge.pipeline.stages.transcribe import read_transcript
from clipforge.queue.errors import PermanentError
from clipforge.scoring.score import ScoredCandidate
from clipforge.scoring.select import select as select_candidates
from clipforge.settings_schema import JobSettings


def _scored(row: ClipCandidateRow) -> ScoredCandidate:
    candidate = ClipCandidate(
        start_s=row.start_ms / 1000,
        end_s=row.end_ms / 1000,
        scores=DimensionScores(
            hook=row.hook_score,
            emotion=row.emotion_score,
            clarity=row.clarity_score,
            relevance=row.relevance_score,
            visual=row.visual_score,
            completeness=row.completeness_score,
        ),
        title=row.title or "",
        hook_line=row.hook_line or "",
        explanation=row.explanation or "",
    )
    return ScoredCandidate(candidate=candidate, total=row.total_score, key=row.id)


class SelectStage:
    """Pure selection over the stored candidates (ADR-010); none qualifying ends the job."""

    name: str = "candidates_selected"
    running_name: str | None = "selecting_candidates"

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def run(self, ctx: StageContext) -> StageResult:
        artifact = await asyncio.to_thread(read_transcript, ctx.workspace)
        if artifact is None:
            raise PermanentError("the transcript is missing; transcription must run first")
        settings = JobSettings.of(ctx.job.settings_snapshot)
        job_id = ctx.job.lease.job_id
        async with self._sessions.begin() as session:
            rows = list(
                await session.scalars(
                    select(ClipCandidateRow)
                    .where(ClipCandidateRow.job_id == job_id)
                    .order_by(ClipCandidateRow.created_at, ClipCandidateRow.id)
                    .with_for_update()
                )
            )
            chosen = select_candidates(
                [_scored(row) for row in rows], artifact.transcript, settings
            )
            await session.execute(
                update(ClipCandidateRow)
                .where(ClipCandidateRow.job_id == job_id)
                .values(selected=False, rank=None)
            )
            for pick in chosen:
                await session.execute(
                    update(ClipCandidateRow)
                    .where(ClipCandidateRow.id == pick.key)
                    .values(
                        selected=True,
                        rank=pick.rank,
                        start_ms=round(pick.candidate.start_s * 1000),
                        end_ms=round(pick.candidate.end_s * 1000),
                    )
                )
        top = [str(pick.total) for pick in chosen]
        return StageResult(
            details={"candidates": len(rows), "selected": len(chosen), "scores": top},
            no_clips=not chosen,
        )
