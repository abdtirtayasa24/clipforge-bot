"""An in-memory Google Drive v3 for respx: metadata, folders and resumable uploads."""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

_APP_PROPERTY = re.compile(r"appProperties has \{ key='(\w+)' and value='([^']*)' \}")
SESSION_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id="


@dataclass
class UploadSession:
    metadata: dict[str, Any]
    size: int
    received: bytearray = field(default_factory=bytearray)


class FakeTokens:
    def __init__(self) -> None:
        self.invalidated = 0

    async def access_token(self) -> str:
        return "access-token"

    def invalidate(self) -> None:
        self.invalidated += 1


class FakeDrive:
    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.content: dict[str, bytes] = {}
        self.sessions: dict[str, UploadSession] = {}
        self.requests: list[httpx.Request] = []
        self.chunk_offsets: list[int] = []
        # Fault injection: drop the connection after this many accepted chunks, or after the
        # final chunk was stored (Drive has the file but the client never hears back).
        self.crash_after_chunks: int | None = None
        self.crash_on_complete = False
        self.corrupt_md5 = False

    def live_files(self, **app_properties: str) -> list[dict[str, Any]]:
        return [
            file
            for file in self.files.values()
            if not file["trashed"]
            and all(file.get("appProperties", {}).get(k) == v for k, v in app_properties.items())
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/upload/drive/v3/files" and request.method == "POST":
            return self._start_session(request)
        if path == "/upload/drive/v3/files" and request.method == "PUT":
            return self._receive(request)
        if path == "/drive/v3/files" and request.method == "GET":
            return self._list(request)
        if path == "/drive/v3/files" and request.method == "POST":
            return httpx.Response(200, json=self._create(json.loads(request.content), b""))
        file_id = path.removeprefix("/drive/v3/files/")
        if file_id not in self.files:
            return httpx.Response(404, json={"error": {"code": 404}})
        if request.method == "PATCH":
            self.files[file_id].update(json.loads(request.content))
        return httpx.Response(200, json=self.files[file_id])

    def _create(self, metadata: dict[str, Any], content: bytes) -> dict[str, Any]:
        file_id = uuid.uuid4().hex
        digest = hashlib.md5(content, usedforsecurity=False).hexdigest()
        file = {
            "id": file_id,
            "name": metadata.get("name"),
            "mimeType": metadata.get("mimeType"),
            "parents": metadata.get("parents", []),
            "appProperties": metadata.get("appProperties", {}),
            "size": str(len(content)),
            "md5Checksum": "0" * 32 if self.corrupt_md5 else digest,
            "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
            "trashed": False,
        }
        self.files[file_id] = file
        self.content[file_id] = content
        return file

    def _list(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        wanted = dict(_APP_PROPERTY.findall(query))
        files = self.live_files(**wanted)
        if "mimeType='application/vnd.google-apps.folder'" in query:
            files = [f for f in files if f["mimeType"] == "application/vnd.google-apps.folder"]
        return httpx.Response(200, json={"files": files})

    def _start_session(self, request: httpx.Request) -> httpx.Response:
        upload_id = uuid.uuid4().hex
        size = int(request.headers["X-Upload-Content-Length"])
        self.sessions[upload_id] = UploadSession(json.loads(request.content), size)
        return httpx.Response(200, headers={"Location": SESSION_URL + upload_id})

    def _receive(self, request: httpx.Request) -> httpx.Response:
        session = self.sessions.get(request.url.params["upload_id"])
        if session is None:
            return httpx.Response(404, json={"error": {"code": 404}})
        content_range = request.headers["Content-Range"]
        if content_range.startswith("bytes */"):
            return self._progress(session)
        start = int(content_range.removeprefix("bytes ").split("-")[0])
        if start != len(session.received):
            return httpx.Response(400, json={"error": {"code": 400}})
        if (
            self.crash_after_chunks is not None
            and len(self.chunk_offsets) >= self.crash_after_chunks
        ):
            raise httpx.ConnectError("connection reset")
        self.chunk_offsets.append(start)
        session.received += request.content
        response = self._progress(session)
        if response.status_code == 200 and self.crash_on_complete:
            raise httpx.ReadError("connection reset after the upload completed")
        return response

    def _progress(self, session: UploadSession) -> httpx.Response:
        if len(session.received) < session.size:
            headers = {"Range": f"bytes=0-{len(session.received) - 1}"} if session.received else {}
            return httpx.Response(308, headers=headers)
        if "file" not in session.metadata:
            session.metadata["file"] = self._create(session.metadata, bytes(session.received))
        return httpx.Response(200, json=session.metadata["file"])
