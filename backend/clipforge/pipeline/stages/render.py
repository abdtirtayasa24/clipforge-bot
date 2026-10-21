import asyncio
import logging
import re
import unicodedata
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.ai.types import TranscriptResult
from clipforge.checksums import file_md5, file_sha256
from clipforge.db.models import Clip, ClipArtifact, MediaAsset
from clipforge.db.models import ClipCandidate as ClipCandidateRow
from clipforge.media.render import render_clip
from clipforge.media.subtitles import build_ass
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import StageResult, verified_artifact, write_artifact
from clipforge.pipeline.stages.transcribe import read_transcript
from clipforge.queue.errors import PermanentError
from clipforge.settings_schema import JobSettings

logger = logging.getLogger(__name__)

CLIPS_DIR = "clips"
MAX_NAME_TITLE_CHARS = 80
_UNSAFE_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def drive_file_name(title: str | None, clip_id: uuid.UUID) -> str:
    """A readable Drive file name; the short clip id keeps same-titled clips apart."""
    cleaned = _UNSAFE_NAME_CHARS.sub(" ", unicodedata.normalize("NFKC", title or ""))
    name = " ".join(cleaned.split())[:MAX_NAME_TITLE_CHARS].strip() or "clip"
    return f"{name} - {clip_id.hex[:8]}.mp4"


def clip_path(workspace: Path, clip_id: uuid.UUID) -> Path:
    return workspace / CLIPS_DIR / f"{clip_id}.mp4"


class RenderStage:
    """Renders the selected candidates one at a time (ADR-008, ADR-013); a restart keeps
    every clip whose verified file, or verified Drive upload, already exists."""

    name: str = "rendering"
    running_name: str | None = None

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def run(self, ctx: StageContext) -> StageResult:
        artifact = await asyncio.to_thread(read_transcript, ctx.workspace)
        if artifact is None:
            raise PermanentError("the transcript is missing; transcription must run first")
        async with self._sessions() as session:
            candidates = list(
                await session.scalars(
                    select(ClipCandidateRow)
                    .where(
                        ClipCandidateRow.job_id == ctx.job.lease.job_id,
                        ClipCandidateRow.selected,
                    )
                    .order_by(ClipCandidateRow.rank)
                )
            )
            asset = (
                await session.get(MediaAsset, ctx.job.media_asset_id)
                if ctx.job.media_asset_id
                else None
            )
        source = Path(asset.local_path) if asset and asset.local_path else None
        if source is None or not await asyncio.to_thread(source.is_file):
            raise PermanentError("the validated source file is missing")
        if not candidates:
            raise PermanentError("no selected candidates to render")
        await asyncio.to_thread(
            (ctx.workspace / CLIPS_DIR).mkdir, mode=0o700, parents=True, exist_ok=True
        )
        title_overlay = JobSettings.of(ctx.job.settings_snapshot).title_overlay_enabled
        rendered = 0
        for candidate in candidates:
            await ctx.check_cancelled()
            clip_id = await self._clip_for(ctx, candidate)
            if await self._finished(clip_id):
                continue
            title = candidate.title if title_overlay else None
            await self._render(
                ctx.workspace, source, artifact.transcript, clip_id, candidate, title
            )
            rendered += 1
        return StageResult(
            details={
                "clips": len(candidates),
                "rendered": rendered,
                "reused": len(candidates) - rendered,
            }
        )

    async def _clip_for(self, ctx: StageContext, candidate: ClipCandidateRow) -> uuid.UUID:
        """The clip row is created before rendering so its id, and so its path, survive a crash."""
        async with self._sessions.begin() as session:
            clip_id: uuid.UUID | None = await session.scalar(
                insert(Clip)
                .values(
                    candidate_id=candidate.id,
                    job_id=ctx.job.lease.job_id,
                    video_id=ctx.job.video_id,
                    start_ms=candidate.start_ms,
                    end_ms=candidate.end_ms,
                    title=candidate.title,
                )
                .on_conflict_do_nothing(index_elements=[Clip.candidate_id])
                .returning(Clip.id)
            )
            if clip_id is None:
                existing = await session.execute(
                    select(Clip.id).where(Clip.candidate_id == candidate.id)
                )
                clip_id = existing.scalar_one()
        return clip_id

    async def _finished(self, clip_id: uuid.UUID) -> bool:
        async with self._sessions() as session:
            artifact = await session.scalar(
                select(ClipArtifact).where(
                    ClipArtifact.clip_id == clip_id, ClipArtifact.kind == "video"
                )
            )
        if artifact is None:
            return False
        if artifact.upload_state == "verified":
            return True
        return artifact.local_path is not None and await verified_artifact(
            Path(artifact.local_path), artifact.sha256
        )

    async def _render(
        self,
        workspace: Path,
        source: Path,
        transcript: TranscriptResult,
        clip_id: uuid.UUID,
        candidate: ClipCandidateRow,
        title: str | None,
    ) -> None:
        start_s, end_s = candidate.start_ms / 1000, candidate.end_ms / 1000
        dest = clip_path(workspace, clip_id)
        partial = dest.with_name(f"{clip_id}.part.mp4")
        subtitles = dest.with_name(f"{clip_id}.ass")
        await write_artifact(subtitles, build_ass(transcript, start_s, end_s, title))
        await render_clip(source, partial, subtitles, start_s, end_s)
        await asyncio.to_thread(partial.replace, dest)
        values = {
            "local_path": str(dest),
            "size_bytes": (await asyncio.to_thread(dest.stat)).st_size,
            "sha256": await asyncio.to_thread(file_sha256, dest),
            "md5": await asyncio.to_thread(file_md5, dest),
            "upload_state": "pending",
            "drive_resumable_uri": None,
            "drive_file_id": None,
            "local_deleted_at": None,
        }
        async with self._sessions.begin() as session:
            await session.execute(
                insert(ClipArtifact)
                .values(clip_id=clip_id, kind="video", **values)
                .on_conflict_do_update(
                    index_elements=[ClipArtifact.clip_id, ClipArtifact.kind], set_=values
                )
            )
        logger.info("clip rendered", extra={"clip_id": str(clip_id)})
