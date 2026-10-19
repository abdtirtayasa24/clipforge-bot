import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.app_settings import permitted_download_domains
from clipforge.db.models import MediaAsset, Video
from clipforge.ingest.disk import projected_usage, reserved_disk_bytes, workspace_usage
from clipforge.ingest.download import download
from clipforge.ingest.uploads import ensure_private_dir, source_path
from clipforge.ingest.validation import validate_media
from clipforge.media.probe import probe
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import StageResult, verified_artifact
from clipforge.queue.errors import PermanentError, TransientError

logger = logging.getLogger(__name__)


class IngestStage:
    """Makes the job's authorized source available locally and validated (ADR-006, ADR-018)."""

    name: str = "validated"
    running_name: str | None = "ingesting"

    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, workspace_root: Path):
        self._sessions = sessions
        self._workspace_root = workspace_root

    async def run(self, ctx: StageContext) -> StageResult:
        if ctx.job.media_asset_id is None:
            raise PermanentError("job has no authorized media source")
        async with self._sessions() as session:
            asset = await session.get_one(MediaAsset, ctx.job.media_asset_id)
            video = await session.get_one(Video, asset.video_id)
        if video.rights_status == "rejected":
            raise PermanentError("the video's rights were rejected")

        local = Path(asset.local_path) if asset.local_path else None
        if asset.state == "ready" and local and await verified_artifact(local, asset.sha256):
            return StageResult(details={"media_asset_id": str(asset.id), "downloaded": False})
        if asset.kind == "upload" or asset.source_url is None:
            raise PermanentError("uploaded source is missing or does not match its checksum")
        await ctx.check_cancelled()
        return await self._download(asset.id, asset.video_id, asset.source_url)

    async def _max_download_bytes(self, session: AsyncSession) -> int:
        await asyncio.to_thread(ensure_private_dir, self._workspace_root / "sources")
        usage = await asyncio.to_thread(workspace_usage, self._workspace_root / "sources")
        # Room left after the reserve and the audio/clip outputs of the longest source.
        overhead = projected_usage(1, duration_s=None) - 1
        available = usage.free - await reserved_disk_bytes(session) - overhead
        if available <= 0:
            raise TransientError("not enough free disk for a download; retrying later")
        return available

    async def _download(self, asset_id: uuid.UUID, video_id: uuid.UUID, url: str) -> StageResult:
        target = source_path(self._workspace_root, asset_id)
        async with self._sessions() as session:
            allowlist = await permitted_download_domains(session)
            max_bytes = await self._max_download_bytes(session)
        try:
            result = await download(url, target, allowlist=allowlist, max_bytes=max_bytes)
            info = await probe(target)
            validate_media(info)
        except PermanentError as exc:
            await asyncio.to_thread(target.unlink, missing_ok=True)
            async with self._sessions.begin() as session:
                await session.execute(
                    update(MediaAsset)
                    .where(MediaAsset.id == asset_id)
                    .values(state="failed", failure_reason=str(exc))
                )
            raise
        async with self._sessions.begin() as session:
            await session.execute(
                update(MediaAsset)
                .where(MediaAsset.id == asset_id)
                .values(
                    state="ready",
                    local_path=str(target),
                    sha256=result.sha256,
                    size_bytes=result.size_bytes,
                    received_bytes=result.size_bytes,
                    media_info=info.model_dump(mode="json"),
                    failure_reason=None,
                )
            )
            await session.execute(
                update(Video).where(Video.id == video_id).values(rights_status="authorized")
            )
        logger.info("source downloaded", extra={"media_asset_id": str(asset_id)})
        return StageResult(
            details={
                "media_asset_id": str(asset_id),
                "downloaded": True,
                "size_bytes": result.size_bytes,
            }
        )
