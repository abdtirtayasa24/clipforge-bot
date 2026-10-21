"""Chunked, resumable uploads of creator-authorized media (ADR-017)."""

import asyncio
import contextlib
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from clipforge.checksums import file_sha256
from clipforge.db.models import MediaAsset, Video
from clipforge.ingest.validation import validate_media
from clipforge.media.probe import probe
from clipforge.queue.errors import PermanentError

logger = logging.getLogger(__name__)

MAX_CHUNK_BYTES = 8 * 1024 * 1024
_ONE_RECEIVING_UPLOAD = "media_assets_one_receiving_upload"
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


class UploadNotFound(Exception):
    pass


class UploadConflict(Exception):
    def __init__(self, message: str, received_bytes: int) -> None:
        super().__init__(message)
        self.received_bytes = received_bytes


class InvalidChunk(Exception):
    pass


class ChecksumMismatch(Exception):
    pass


class UploadInProgress(Exception):
    pass


@dataclass(frozen=True)
class ContentRange:
    start: int
    end: int
    total: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_content_range(header: str | None) -> ContentRange:
    match = _CONTENT_RANGE.match(header or "")
    if match is None:
        raise InvalidChunk("Content-Range must be 'bytes start-end/total'")
    start, end, total = (int(group) for group in match.groups())
    if start > end or end >= total:
        raise InvalidChunk("Content-Range is out of bounds")
    return ContentRange(start, end, total)


def staging_path(workspace: Path, upload_id: uuid.UUID) -> Path:
    return workspace / "uploads" / str(upload_id)


def source_path(workspace: Path, asset_id: uuid.UUID) -> Path:
    return workspace / "sources" / str(asset_id)


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _create_staging_file(path: Path) -> None:
    ensure_private_dir(path.parent)
    path.touch(mode=0o600, exist_ok=False)


def _write_at(path: Path, offset: int, data: bytes) -> None:
    with path.open("r+b") as file:
        file.seek(offset)
        file.write(data)
        file.truncate()
        file.flush()
        os.fsync(file.fileno())


def _promote(staging: Path, target: Path) -> None:
    ensure_private_dir(target.parent)
    staging.replace(target)


def _remove(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def evidence_record(admin_user_id: uuid.UUID) -> dict[str, Any]:
    """Who registered the source and when; no permission statement is asked for (ADR-031)."""
    return {
        "recorded_by": str(admin_user_id),
        "recorded_at": datetime.now(UTC).isoformat(),
    }


async def create_upload(
    session: AsyncSession,
    workspace: Path,
    *,
    video_id: uuid.UUID,
    filename: str,
    size_bytes: int,
    sha256: str,
    evidence: dict[str, Any],
) -> MediaAsset:
    upload_id = uuid.uuid4()
    # The client's filename is display-only; files are always named by UUID.
    staging = staging_path(workspace, upload_id)
    asset = MediaAsset(
        id=upload_id,
        video_id=video_id,
        kind="upload",
        original_filename=filename,
        local_path=str(staging),
        size_bytes=size_bytes,
        sha256=sha256,
        permission_evidence=evidence,
        state="receiving",
    )
    session.add(asset)
    try:
        await session.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == _ONE_RECEIVING_UPLOAD:
            raise UploadInProgress("another upload is in progress") from None
        raise
    await asyncio.to_thread(_create_staging_file, staging)
    await session.commit()
    logger.info("upload started", extra={"upload_id": str(upload_id), "size_bytes": size_bytes})
    return asset


async def active_upload(session: AsyncSession) -> MediaAsset | None:
    """The one upload still receiving bytes, if any (ADR-032)."""
    return await session.scalar(
        select(MediaAsset).where(MediaAsset.kind == "upload", MediaAsset.state == "receiving")
    )


async def _locked_upload(session: AsyncSession, upload_id: uuid.UUID) -> MediaAsset:
    asset = await session.scalar(
        select(MediaAsset)
        .where(MediaAsset.id == upload_id, MediaAsset.kind == "upload")
        .with_for_update()
    )
    if asset is None:
        raise UploadNotFound(str(upload_id))
    return asset


def _require_receiving(asset: MediaAsset) -> None:
    if asset.state != "receiving":
        raise UploadConflict(f"upload is {asset.state}", asset.received_bytes)


async def append_chunk(
    session: AsyncSession, upload_id: uuid.UUID, content_range: ContentRange, data: bytes
) -> MediaAsset:
    asset = await _locked_upload(session, upload_id)
    _require_receiving(asset)
    if content_range.total != asset.size_bytes or content_range.length != len(data):
        raise InvalidChunk("Content-Range does not match the upload size or chunk length")
    if content_range.start != asset.received_bytes:
        raise UploadConflict("chunk does not start at the received offset", asset.received_bytes)
    await asyncio.to_thread(_write_at, Path(str(asset.local_path)), content_range.start, data)
    asset.received_bytes = content_range.end + 1
    await session.commit()
    return asset


async def _fail(session: AsyncSession, asset: MediaAsset, reason: str) -> None:
    asset.state = "failed"
    asset.failure_reason = reason
    await asyncio.to_thread(_remove, Path(str(asset.local_path)))
    await session.commit()
    logger.warning("upload failed", extra={"upload_id": str(asset.id), "reason": reason})


async def cancel_upload(session: AsyncSession, upload_id: uuid.UUID) -> MediaAsset:
    """Stops an upload and frees its staging file so the next upload can start."""
    asset = await _locked_upload(session, upload_id)
    _require_receiving(asset)
    await _fail(session, asset, "cancelled")
    return asset


async def complete_upload(
    session: AsyncSession, upload_id: uuid.UUID, workspace: Path
) -> MediaAsset:
    """Verifies size, checksum and media, then marks the asset ready and the video authorized."""
    asset = await _locked_upload(session, upload_id)
    _require_receiving(asset)
    if asset.received_bytes != asset.size_bytes:
        raise UploadConflict("upload is incomplete", asset.received_bytes)
    staging = Path(str(asset.local_path))
    if await asyncio.to_thread(file_sha256, staging) != asset.sha256:
        await _fail(session, asset, "checksum mismatch")
        raise ChecksumMismatch("uploaded data does not match the declared sha256")
    try:
        info = await probe(staging)
        validate_media(info)
    except PermanentError as exc:
        await _fail(session, asset, str(exc))
        raise
    target = source_path(workspace, asset.id)
    await asyncio.to_thread(_promote, staging, target)
    asset.local_path = str(target)
    asset.media_info = info.model_dump(mode="json")
    asset.state = "ready"
    await session.execute(
        update(Video).where(Video.id == asset.video_id).values(rights_status="authorized")
    )
    await session.commit()
    logger.info("upload completed", extra={"upload_id": str(asset.id)})
    return asset
