"""Google Drive v3 for clip uploads (ADR-014): resumable sessions that survive restarts, and
an app property that lets a rerun find a file whose upload already finished."""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.app_settings import get_setting
from clipforge.db.models import AppSetting
from clipforge.queue.errors import TransientError
from clipforge.storage.drive_oauth import FOLDER_KEY, FOLDER_NAME

logger = logging.getLogger(__name__)

FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
CLIP_MIME = "video/mp4"
CLIP_ID_PROPERTY = "clipforge_clip_id"
FOLDER_PROPERTY = "clipforge_folder"
FILE_FIELDS = "id,name,size,md5Checksum,webViewLink"
# Drive requires chunks in multiples of 256 KiB (except the last one).
MIN_CHUNK_BYTES = 256 * 1024
CHUNK_BYTES = 32 * MIN_CHUNK_BYTES
HTTP_TIMEOUT_S = 120.0
SESSION_GONE = (404, 410)


class RemoteFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str | None = None
    size: int | None = None
    md5_checksum: str | None = Field(default=None, alias="md5Checksum")
    web_view_link: str | None = Field(default=None, alias="webViewLink")


class TokenSource(Protocol):
    async def access_token(self) -> str: ...

    def invalidate(self) -> None: ...


class UploadSessionExpired(Exception):
    pass


def _app_property(key: str, value: str) -> str:
    return f"appProperties has {{ key='{key}' and value='{value}' }}"


def _next_offset(response: httpx.Response) -> int:
    """Drive confirms the bytes it kept as `Range: bytes=0-N`; no header means none."""
    received = response.headers.get("Range")
    return int(received.rsplit("-", 1)[1]) + 1 if received else 0


def _read_chunk(path: Path, offset: int, length: int) -> bytes:
    with path.open("rb") as file:
        file.seek(offset)
        return file.read(length)


class DriveClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: TokenSource,
        sessions: async_sessionmaker[AsyncSession],
        *,
        chunk_bytes: int = CHUNK_BYTES,
    ) -> None:
        if chunk_bytes % MIN_CHUNK_BYTES:
            raise ValueError("chunk_bytes must be a multiple of 256 KiB")
        self._http = http
        self._tokens = tokens
        self._sessions = sessions
        self._chunk_bytes = chunk_bytes

    async def _request(
        self, method: str, url: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        token = await self._tokens.access_token()
        try:
            response = await self._http.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}", **(headers or {})},
                timeout=HTTP_TIMEOUT_S,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise TransientError(f"Google Drive unreachable: {type(exc).__name__}") from None
        if response.status_code == 401:
            self._tokens.invalidate()
        return response

    @staticmethod
    def _failed(response: httpx.Response, action: str) -> TransientError:
        return TransientError(f"Google Drive {action} failed with HTTP {response.status_code}")

    async def _files(self, query: str) -> list[RemoteFile]:
        response = await self._request(
            "GET",
            FILES_URL,
            params={"q": query, "spaces": "drive", "fields": f"files({FILE_FIELDS})"},
        )
        if not response.is_success:
            raise self._failed(response, "search")
        return [RemoteFile.model_validate(item) for item in response.json().get("files", [])]

    async def folder_id(self) -> str:
        """The app's own upload folder, created on first use (ADR-015: `drive.file` scope)."""
        async with self._sessions() as session:
            stored = await get_setting(session, FOLDER_KEY)
        if isinstance(stored, dict) and isinstance(stored.get("id"), str):
            return str(stored["id"])
        existing = await self._files(
            f"mimeType='{FOLDER_MIME}' and {_app_property(FOLDER_PROPERTY, 'clips')} "
            "and trashed=false"
        )
        if existing:
            folder_id = existing[0].id
        else:
            response = await self._request(
                "POST",
                FILES_URL,
                params={"fields": "id"},
                json={
                    "name": FOLDER_NAME,
                    "mimeType": FOLDER_MIME,
                    "appProperties": {FOLDER_PROPERTY: "clips"},
                },
            )
            if not response.is_success:
                raise self._failed(response, "folder creation")
            folder_id = str(response.json()["id"])
        value = {"id": folder_id, "name": FOLDER_NAME}
        async with self._sessions.begin() as session:
            await session.execute(
                insert(AppSetting)
                .values(key=FOLDER_KEY, value=value)
                .on_conflict_do_update(index_elements=["key"], set_={"value": value})
            )
        return folder_id

    async def find_clip(self, clip_id: uuid.UUID) -> list[RemoteFile]:
        return await self._files(
            f"{_app_property(CLIP_ID_PROPERTY, str(clip_id))} and trashed=false"
        )

    async def trash(self, file_id: str) -> None:
        response = await self._request(
            "PATCH", f"{FILES_URL}/{file_id}", params={"fields": "id"}, json={"trashed": True}
        )
        if not response.is_success and response.status_code != 404:
            raise self._failed(response, "trash")

    async def start_upload(self, name: str, folder_id: str, clip_id: uuid.UUID, size: int) -> str:
        """A new resumable session; the returned URI is the only handle on it."""
        response = await self._request(
            "POST",
            UPLOAD_URL,
            params={"uploadType": "resumable", "fields": FILE_FIELDS},
            headers={"X-Upload-Content-Type": CLIP_MIME, "X-Upload-Content-Length": str(size)},
            json={
                "name": name,
                "mimeType": CLIP_MIME,
                "parents": [folder_id],
                "appProperties": {CLIP_ID_PROPERTY: str(clip_id)},
            },
        )
        location: str | None = response.headers.get("Location")
        if not response.is_success or not location:
            raise self._failed(response, "upload start")
        return location

    def _finished(self, response: httpx.Response, action: str) -> RemoteFile | int:
        if response.status_code in (200, 201):
            return RemoteFile.model_validate(response.json())
        if response.status_code == 308:
            return _next_offset(response)
        if response.status_code in SESSION_GONE:
            raise UploadSessionExpired
        raise self._failed(response, action)

    async def resume(self, session_uri: str, path: Path, size: int) -> RemoteFile:
        """Asks Drive how much of an interrupted session it kept, then sends the rest."""
        response = await self._request(
            "PUT", session_uri, headers={"Content-Range": f"bytes */{size}"}, content=b""
        )
        progress = self._finished(response, "upload status")
        if isinstance(progress, RemoteFile):
            return progress
        return await self.upload(session_uri, path, size, offset=progress)

    async def upload(self, session_uri: str, path: Path, size: int, *, offset: int) -> RemoteFile:
        while True:
            chunk = await asyncio.to_thread(_read_chunk, path, offset, self._chunk_bytes)
            if not chunk:
                raise TransientError("the local clip is shorter than the upload session")
            end = offset + len(chunk) - 1
            response = await self._request(
                "PUT",
                session_uri,
                headers={"Content-Range": f"bytes {offset}-{end}/{size}"},
                content=chunk,
            )
            progress = self._finished(response, "chunk upload")
            if isinstance(progress, RemoteFile):
                return progress
            if progress <= offset:
                raise TransientError("Google Drive accepted none of the chunk")
            offset = progress
