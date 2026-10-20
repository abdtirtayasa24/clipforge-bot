import asyncio
import logging
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.checksums import file_sha256
from clipforge.db.models import MediaAsset
from clipforge.media.audio import (
    CHUNK_OVERLAP_S,
    DEFAULT_CHUNK_MINUTES,
    AudioChunk,
    AudioManifest,
    extract_chunk,
    plan_chunks,
)
from clipforge.media.probe import probe
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import StageResult, verified_artifact
from clipforge.queue.errors import PermanentError

logger = logging.getLogger(__name__)

AUDIO_DIR = "audio"
MANIFEST_NAME = "manifest.json"


def manifest_path(workspace: Path) -> Path:
    return workspace / AUDIO_DIR / MANIFEST_NAME


def read_manifest(path: Path) -> AudioManifest | None:
    try:
        return AudioManifest.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


async def _reusable(
    manifest: AudioManifest, audio_dir: Path, source_sha256: str, chunk_s: float
) -> bool:
    if (manifest.source_sha256, manifest.chunk_s) != (source_sha256, chunk_s):
        return False
    for chunk in manifest.chunks:
        if not await verified_artifact(audio_dir / chunk.file, chunk.sha256):
            return False
    return True


class AudioStage:
    """Extracts the source's audio as overlapping transcription chunks (ADR-012)."""

    name: str = "audio_extracted"
    running_name: str | None = None

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def run(self, ctx: StageContext) -> StageResult:
        if ctx.job.media_asset_id is None:
            raise PermanentError("job has no authorized media source")
        async with self._sessions() as session:
            asset = await session.get_one(MediaAsset, ctx.job.media_asset_id)
        source = Path(asset.local_path) if asset.local_path else None
        if source is None or asset.sha256 is None or not await asyncio.to_thread(source.is_file):
            raise PermanentError("the validated source file is missing")
        minutes = ctx.job.settings_snapshot.get("audio_chunk_minutes", DEFAULT_CHUNK_MINUTES)
        chunk_s = float(minutes) * 60

        audio_dir = ctx.workspace / AUDIO_DIR
        path = manifest_path(ctx.workspace)
        existing = await asyncio.to_thread(read_manifest, path)
        if existing and await _reusable(existing, audio_dir, asset.sha256, chunk_s):
            return StageResult(details={"chunks": len(existing.chunks), "reused": True})

        duration_s = (asset.media_info or {}).get("duration_s") or (await probe(source)).duration_s
        if not duration_s:
            raise PermanentError("the source duration is unknown")
        await asyncio.to_thread(audio_dir.mkdir, mode=0o700, exist_ok=True)
        chunks = []
        for plan in plan_chunks(duration_s, chunk_s=chunk_s):
            await ctx.check_cancelled()
            dest = audio_dir / f"chunk_{plan.index:03d}.mp3"
            await extract_chunk(source, dest, plan)
            chunks.append(
                AudioChunk(
                    index=plan.index,
                    file=dest.name,
                    start_s=plan.start_s,
                    end_s=plan.end_s,
                    sha256=await asyncio.to_thread(file_sha256, dest),
                    size_bytes=(await asyncio.to_thread(dest.stat)).st_size,
                )
            )
        manifest = AudioManifest(
            source_sha256=asset.sha256,
            duration_s=duration_s,
            chunk_s=chunk_s,
            overlap_s=CHUNK_OVERLAP_S,
            chunks=chunks,
        )
        partial = path.with_name(path.name + ".part")
        await asyncio.to_thread(partial.write_text, manifest.model_dump_json(indent=2))
        await asyncio.to_thread(partial.replace, path)
        logger.info("audio extracted", extra={"chunks": len(chunks)})
        return StageResult(details={"chunks": len(chunks), "reused": False})
