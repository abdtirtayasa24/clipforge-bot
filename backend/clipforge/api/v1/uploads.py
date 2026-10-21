import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from clipforge.api.deps import DbSession, JobQueueDep, SettingsDep
from clipforge.api.errors import (
    ApiError,
    Conflict,
    InsufficientStorage,
    NotFound,
    PayloadTooLarge,
)
from clipforge.auth.deps import CurrentAdmin, require_admin
from clipforge.db.models import MediaAsset, Video
from clipforge.ingest import uploads
from clipforge.ingest.disk import (
    Admission,
    decide_admission,
    reserved_disk_bytes,
    workspace_usage,
)
from clipforge.queue.errors import PermanentError

router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[Depends(require_admin)])

MAX_UPLOAD_BYTES = 64 * 1024**3


class ChecksumMismatchError(ApiError):
    status_code = 422
    code = "checksum_mismatch"


class InvalidMediaError(ApiError):
    status_code = 422
    code = "invalid_media"


class UploadInit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: uuid.UUID
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0, le=MAX_UPLOAD_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class UploadOut(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    filename: str | None
    state: str
    size_bytes: int | None
    received_bytes: int
    failure_reason: str | None
    created_at: datetime

    @classmethod
    def of(cls, asset: MediaAsset) -> "UploadOut":
        return cls(
            id=asset.id,
            video_id=asset.video_id,
            filename=asset.original_filename,
            state=asset.state,
            size_bytes=asset.size_bytes,
            received_bytes=asset.received_bytes,
            failure_reason=asset.failure_reason,
            created_at=asset.created_at,
        )


def _conflict(error: uploads.UploadConflict) -> Conflict:
    return Conflict(str(error), details={"received_bytes": error.received_bytes})


def _in_progress(active: MediaAsset | None) -> Conflict:
    details = (
        None if active is None else {"upload_id": str(active.id), "video_id": str(active.video_id)}
    )
    return Conflict("another upload is in progress", details=details)


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_upload(
    body: UploadInit, session: DbSession, settings: SettingsDep, admin: CurrentAdmin
) -> UploadOut:
    if await session.get(Video, body.video_id) is None:
        raise NotFound("video not found")
    active = await uploads.active_upload(session)
    if active is not None:
        raise _in_progress(active)
    staging_dir = uploads.staging_path(settings.workspace_dir, uuid.uuid4()).parent
    await asyncio.to_thread(uploads.ensure_private_dir, staging_dir)
    decision = decide_admission(
        body.size_bytes,
        duration_s=None,
        usage=await asyncio.to_thread(workspace_usage, staging_dir),
        reserve_bytes=await reserved_disk_bytes(session),
    )
    details = {"projected_bytes": decision.projected_bytes, "free_bytes": decision.free_bytes}
    if decision.verdict is Admission.REJECT:
        raise PayloadTooLarge("source is too large for this server's disk", details=details)
    if decision.verdict is Admission.DEFER:
        raise InsufficientStorage("not enough free disk right now; retry later", details=details)
    try:
        asset = await uploads.create_upload(
            session,
            settings.workspace_dir,
            video_id=body.video_id,
            filename=body.filename,
            size_bytes=body.size_bytes,
            sha256=body.sha256,
            evidence=uploads.evidence_record(admin.id),
        )
    except uploads.UploadInProgress:
        await session.rollback()
        raise _in_progress(await uploads.active_upload(session)) from None
    return UploadOut.of(asset)


@router.get("/active")
async def get_active_upload(session: DbSession) -> UploadOut | None:
    asset = await uploads.active_upload(session)
    return None if asset is None else UploadOut.of(asset)


@router.delete("/{upload_id}")
async def cancel_upload(upload_id: uuid.UUID, session: DbSession) -> UploadOut:
    try:
        asset = await uploads.cancel_upload(session, upload_id)
    except uploads.UploadNotFound:
        raise NotFound("upload not found") from None
    except uploads.UploadConflict as exc:
        raise _conflict(exc) from None
    return UploadOut.of(asset)


@router.get("/{upload_id}")
async def get_upload(upload_id: uuid.UUID, session: DbSession) -> UploadOut:
    asset = await session.get(MediaAsset, upload_id)
    if asset is None or asset.kind != "upload":
        raise NotFound("upload not found")
    return UploadOut.of(asset)


async def _read_chunk(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > uploads.MAX_CHUNK_BYTES:
        raise PayloadTooLarge(f"chunks are limited to {uploads.MAX_CHUNK_BYTES} bytes")
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > uploads.MAX_CHUNK_BYTES:
            raise PayloadTooLarge(f"chunks are limited to {uploads.MAX_CHUNK_BYTES} bytes")
    return bytes(data)


@router.patch("/{upload_id}")
async def upload_chunk(upload_id: uuid.UUID, request: Request, session: DbSession) -> UploadOut:
    try:
        content_range = uploads.parse_content_range(request.headers.get("content-range"))
    except uploads.InvalidChunk as exc:
        raise ApiError(str(exc)) from None
    data = await _read_chunk(request)
    try:
        asset = await uploads.append_chunk(session, upload_id, content_range, data)
    except uploads.UploadNotFound:
        raise NotFound("upload not found") from None
    except uploads.InvalidChunk as exc:
        raise ApiError(str(exc)) from None
    except uploads.UploadConflict as exc:
        raise _conflict(exc) from None
    return UploadOut.of(asset)


@router.post("/{upload_id}/complete")
async def complete_upload(
    upload_id: uuid.UUID, session: DbSession, settings: SettingsDep, queue: JobQueueDep
) -> UploadOut:
    try:
        asset = await uploads.complete_upload(session, upload_id, settings.workspace_dir)
    except uploads.UploadNotFound:
        raise NotFound("upload not found") from None
    except uploads.UploadConflict as exc:
        raise _conflict(exc) from None
    except uploads.ChecksumMismatch as exc:
        raise ChecksumMismatchError(str(exc)) from None
    except PermanentError as exc:
        raise InvalidMediaError(str(exc)) from None
    await queue.attach_source(asset.video_id, asset.id)
    return UploadOut.of(asset)
