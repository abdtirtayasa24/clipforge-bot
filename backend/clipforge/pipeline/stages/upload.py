import asyncio
import logging
import uuid
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.models import Clip, ClipArtifact
from clipforge.db.models import ClipCandidate as ClipCandidateRow
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import StageResult, verified_artifact
from clipforge.pipeline.stages.render import drive_file_name
from clipforge.queue.errors import PermanentError, TransientError
from clipforge.storage.drive import DriveClient, RemoteFile, UploadSessionExpired
from clipforge.storage.drive_oauth import DriveNotConnected

logger = logging.getLogger(__name__)


def _matches(remote: RemoteFile, artifact: ClipArtifact) -> bool:
    return (remote.size, remote.md5_checksum) == (artifact.size_bytes, artifact.md5)


class UploadStage:
    """Uploads each rendered clip to Drive exactly once (ADR-014).

    Every step can be interrupted: a rerun first adopts a matching Drive file tagged with the
    clip id, then resumes the persisted session, and only then starts a new one. The local
    copy is deleted only after Drive reported the same size and MD5.
    """

    name: str = "uploading"
    running_name: str | None = None

    def __init__(self, sessions: async_sessionmaker[AsyncSession], drive: DriveClient | None):
        self._sessions = sessions
        self._drive = drive

    async def run(self, ctx: StageContext) -> StageResult:
        async with self._sessions() as session:
            rows = list(
                await session.execute(
                    select(Clip, ClipArtifact)
                    .join(ClipArtifact, ClipArtifact.clip_id == Clip.id)
                    .join(ClipCandidateRow, ClipCandidateRow.id == Clip.candidate_id)
                    .where(Clip.job_id == ctx.job.lease.job_id, ClipArtifact.kind == "video")
                    .order_by(ClipCandidateRow.rank, Clip.id)
                )
            )
        if not rows:
            raise PermanentError("no rendered clips to upload")
        outcomes: Counter[str] = Counter()
        for clip, artifact in rows:
            await ctx.check_cancelled()
            if artifact.upload_state == "verified":
                outcomes["already_verified"] += 1
            else:
                outcomes[await self._upload(ctx, clip, artifact)] += 1
            await self._delete_local(artifact.id, artifact.local_path)
        return StageResult(
            details={
                "clips": len(rows),
                **{key: outcomes[key] for key in ("uploaded", "adopted", "already_verified")},
            }
        )

    async def _upload(self, ctx: StageContext, clip: Clip, artifact: ClipArtifact) -> str:
        if self._drive is None:
            raise DriveNotConnected("Google Drive OAuth is not configured")
        drive = self._drive
        path = Path(artifact.local_path or "")
        if (
            not artifact.local_path
            or artifact.size_bytes is None
            or not artifact.md5
            or not await verified_artifact(path, artifact.sha256)
        ):
            raise PermanentError("the rendered clip file is missing or changed")
        for remote in await drive.find_clip(clip.id):
            if _matches(remote, artifact):
                await self._mark_verified(clip.id, artifact.id, remote)
                return "adopted"
            # A copy of an earlier render of this clip; replaced below.
            await drive.trash(remote.id)
        uploaded = None
        if artifact.drive_resumable_uri:
            try:
                uploaded = await drive.resume(
                    artifact.drive_resumable_uri, path, artifact.size_bytes
                )
            except UploadSessionExpired:
                logger.info("upload session expired; starting a new one")
        if uploaded is None:
            folder_id = await drive.folder_id()
            # Drive writes are not fenced: a worker that stalled past its lease must not start
            # a second copy while the worker that reclaimed the job uploads this clip.
            await ctx.queue.renew(ctx.job.lease)
            session_uri = await drive.start_upload(
                drive_file_name(clip.title_override or clip.title, clip.id),
                folder_id,
                clip.id,
                artifact.size_bytes,
            )
            await self._set_session(artifact.id, session_uri)
            uploaded = await drive.upload(session_uri, path, artifact.size_bytes, offset=0)
        if not _matches(uploaded, artifact):
            await drive.trash(uploaded.id)
            await self._set_session(artifact.id, None)
            raise TransientError("Drive size or checksum mismatch; the clip will be re-uploaded")
        await self._mark_verified(clip.id, artifact.id, uploaded)
        return "uploaded"

    async def _set_session(self, artifact_id: uuid.UUID, session_uri: str | None) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(ClipArtifact)
                .where(ClipArtifact.id == artifact_id)
                .values(
                    upload_state="uploading" if session_uri else "pending",
                    drive_resumable_uri=session_uri,
                )
            )

    async def _mark_verified(
        self, clip_id: uuid.UUID, artifact_id: uuid.UUID, remote: RemoteFile
    ) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(ClipArtifact)
                .where(ClipArtifact.id == artifact_id)
                .values(upload_state="verified", drive_file_id=remote.id, drive_resumable_uri=None)
            )
            await session.execute(
                update(Clip)
                .where(Clip.id == clip_id)
                .values(drive_file_id=remote.id, drive_web_view_link=remote.web_view_link)
            )
        logger.info("clip uploaded", extra={"clip_id": str(clip_id), "drive_file_id": remote.id})

    async def _delete_local(self, artifact_id: uuid.UUID, local_path: str | None) -> None:
        """Only for verified artifacts: the database refuses `local_deleted_at` otherwise."""
        if local_path:
            await asyncio.to_thread(Path(local_path).unlink, missing_ok=True)
        async with self._sessions.begin() as session:
            await session.execute(
                update(ClipArtifact)
                .where(
                    ClipArtifact.id == artifact_id,
                    ClipArtifact.upload_state == "verified",
                    ClipArtifact.local_deleted_at.is_(None),
                )
                .values(local_deleted_at=func.now())
            )
