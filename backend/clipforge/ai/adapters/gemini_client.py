"""Gemini REST calls shared by the Gemini adapters: retries, 429 handling, usage (ADR-007, 028)."""

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from clipforge.ai.errors import ModelUnavailable
from clipforge.ai.quota import QuotaGuard, retry_after_seconds
from clipforge.ai.registry import ProviderConfigError
from clipforge.ai.usage import ProviderCall, UsageRecorder
from clipforge.queue.errors import PermanentError
from clipforge.settings_schema import ProviderSnapshot

logger = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com"
API_VERSION = "v1beta"
MAX_ATTEMPTS = 3
RETRY_DELAYS_S = (2.0, 8.0)
# Longer 429 waits defer the job instead of holding the worker (ADR-028).
MAX_INLINE_RATE_LIMIT_WAIT_S = 60.0
REQUEST_TIMEOUT = httpx.Timeout(30.0, read=600.0)
# Inline requests are capped at 20 MB in total; base64 grows data by a third.
MAX_INLINE_BYTES = 14 * 1024 * 1024
FILE_POLL_INTERVAL_S = 2.0
FILE_POLL_ATTEMPTS = 60
_RETRYABLE_STATUSES = frozenset({500, 502, 503, 504})

Sleep = Callable[[float], Awaitable[None]]


def _duration_seconds(value: object) -> float | None:
    if isinstance(value, str) and value.endswith("s"):
        try:
            return float(value[:-1])
        except ValueError:
            return None
    return None


def body_retry_delay(response: httpx.Response) -> float | None:
    """`RetryInfo.retryDelay` from a Google error body, e.g. "33s"."""
    try:
        details = response.json()["error"]["details"]
    except (ValueError, KeyError, TypeError):
        return None
    for detail in details if isinstance(details, list) else []:
        if isinstance(detail, dict) and "retryDelay" in detail:
            return _duration_seconds(detail["retryDelay"])
    return None


def _error_status(response: httpx.Response) -> str:
    try:
        status = response.json()["error"]["status"]
    except (ValueError, KeyError, TypeError):
        return str(response.status_code)
    return status if isinstance(status, str) else str(response.status_code)


def _backoff(attempt: int) -> float:
    return RETRY_DELAYS_S[min(attempt, len(RETRY_DELAYS_S)) - 1]


def usage_tokens(body: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = body.get("usageMetadata") or {}
    return usage.get("promptTokenCount"), usage.get("candidatesTokenCount")


class GeminiClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        provider: ProviderSnapshot,
        *,
        quota: QuotaGuard,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        # Sent as a header so the key never appears in URLs or logs.
        self._headers = {"x-goog-api-key": api_key}
        self.provider = provider
        self._quota = quota
        self._sleep = sleep

    async def generate(
        self,
        body: dict[str, Any],
        *,
        record: UsageRecorder,
        audio_seconds: float | None = None,
        method: str = "generateContent",
    ) -> dict[str, Any]:
        """POSTs a model method, recording every attempt; raises from the error taxonomy."""
        url = f"{BASE_URL}/{API_VERSION}/models/{self.provider.model}:{method}"
        model = self.provider.model
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._quota.acquire(self.provider)
            started = time.monotonic()
            try:
                response = await self._http.post(
                    url, json=body, headers=self._headers, timeout=REQUEST_TIMEOUT
                )
            except httpx.TransportError as exc:
                await record(self._call(started, False, error_code=type(exc).__name__))
                error: Exception = ModelUnavailable(f"{model} request failed: {type(exc).__name__}")
                retry_wait: float | None = _backoff(attempt)
            else:
                if response.is_success:
                    result: dict[str, Any] = response.json()
                    tokens_in, tokens_out = usage_tokens(result)
                    await record(
                        self._call(
                            started,
                            True,
                            input_tokens=tokens_in,
                            output_tokens=tokens_out,
                            audio_seconds=audio_seconds,
                        )
                    )
                    return result
                await record(self._call(started, False, error_code=str(response.status_code)))
                error, retry_wait = self._error(response, attempt)
            if retry_wait is None or attempt == MAX_ATTEMPTS:
                raise error
            logger.warning(
                "gemini request failed; retrying",
                extra={"model": model, "attempt": attempt, "wait_s": retry_wait},
            )
            await self._sleep(retry_wait)
        raise AssertionError("unreachable")

    def _error(self, response: httpx.Response, attempt: int) -> tuple[Exception, float | None]:
        """The error to raise, and how long to wait first when it is worth retrying."""
        model, code = self.provider.model, response.status_code
        status = _error_status(response)
        if code == 429:
            wait = retry_after_seconds(response.headers, self._quota.now())
            wait = wait if wait is not None else body_retry_delay(response)
            deferral = self._quota.deferral(self.provider, wait)
            if wait is not None and wait <= MAX_INLINE_RATE_LIMIT_WAIT_S:
                return deferral, wait
            return deferral, None
        if code in _RETRYABLE_STATUSES:
            return ModelUnavailable(f"{model} returned {code} {status}"), _backoff(attempt)
        if code == 404:
            return ModelUnavailable(f"{model} is not available ({status})"), None
        if code in (401, 403):
            return ProviderConfigError(f"{model} rejected the API key ({status})"), None
        return PermanentError(f"{model} rejected the request: {code} {status}"), None

    def _call(self, started: float, success: bool, **fields: Any) -> ProviderCall:
        latency_ms = int((time.monotonic() - started) * 1000)
        return ProviderCall(success=success, latency_ms=latency_ms, **fields)

    async def audio_part(self, path: Path, mime_type: str) -> dict[str, Any]:
        """Inline data for small files, otherwise a Files API reference."""
        size = (await asyncio.to_thread(path.stat)).st_size
        if size <= MAX_INLINE_BYTES:
            data = await asyncio.to_thread(path.read_bytes)
            return {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(data).decode()}}
        uri = await self.upload_file(path, mime_type, size)
        return {"fileData": {"mimeType": mime_type, "fileUri": uri}}

    async def upload_file(self, path: Path, mime_type: str, size: int) -> str:
        try:
            start = await self._http.post(
                f"{BASE_URL}/upload/{API_VERSION}/files",
                json={"file": {"display_name": path.name}},
                headers=self._headers
                | {
                    "X-Goog-Upload-Protocol": "resumable",
                    "X-Goog-Upload-Command": "start",
                    "X-Goog-Upload-Header-Content-Length": str(size),
                    "X-Goog-Upload-Header-Content-Type": mime_type,
                },
                timeout=REQUEST_TIMEOUT,
            )
            upload_url = start.headers.get("x-goog-upload-url")
            if not start.is_success or not upload_url:
                raise ModelUnavailable(f"Files API upload could not start: {start.status_code}")
            data = await asyncio.to_thread(path.read_bytes)
            finished = await self._http.post(
                upload_url,
                content=data,
                headers=self._headers
                | {"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
                timeout=REQUEST_TIMEOUT,
            )
            if not finished.is_success:
                raise ModelUnavailable(f"Files API upload failed: {finished.status_code}")
            file = finished.json()["file"]
            return await self._wait_active(file)
        except httpx.TransportError as exc:
            raise ModelUnavailable(f"Files API upload failed: {type(exc).__name__}") from None

    async def _wait_active(self, file: dict[str, Any]) -> str:
        for _ in range(FILE_POLL_ATTEMPTS):
            if file.get("state", "ACTIVE") == "ACTIVE":
                return str(file["uri"])
            if file.get("state") == "FAILED":
                raise PermanentError("the Files API could not process the audio")
            await self._sleep(FILE_POLL_INTERVAL_S)
            response = await self._http.get(
                f"{BASE_URL}/{API_VERSION}/{file['name']}",
                headers=self._headers,
                timeout=REQUEST_TIMEOUT,
            )
            if not response.is_success:
                raise ModelUnavailable(f"Files API status check failed: {response.status_code}")
            file = response.json()
        raise ModelUnavailable("uploaded audio did not become active in time")
