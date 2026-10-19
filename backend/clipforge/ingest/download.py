"""Streams a permitted URL to disk through the SSRF guard (ADR-018)."""

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import httpx

from clipforge.queue.errors import PermanentError, TransientError
from clipforge.security.url_guard import GuardedTransport, UrlRejected, check_url

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
DOWNLOAD_TIMEOUT_S = 3 * 60 * 60
_TIMEOUTS = httpx.Timeout(30.0, read=120.0)


@dataclass(frozen=True)
class DownloadResult:
    sha256: str
    size_bytes: int


def _remove(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


async def _save(response: httpx.Response, partial: Path, max_bytes: int) -> DownloadResult:
    digest = hashlib.sha256()
    size = 0
    file: BinaryIO = await asyncio.to_thread(partial.open, "wb")
    try:
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise PermanentError(f"download exceeds the {max_bytes} byte limit")
            digest.update(chunk)
            await asyncio.to_thread(file.write, chunk)
        await asyncio.to_thread(file.flush)
    finally:
        await asyncio.to_thread(file.close)
    return DownloadResult(digest.hexdigest(), size)


async def _fetch(
    client: httpx.AsyncClient, url: str, dest: Path, allowlist: Sequence[str], max_bytes: int
) -> DownloadResult:
    current = check_url(url, allowlist)
    for _ in range(MAX_REDIRECTS + 1):
        async with client.stream("GET", current) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise PermanentError("redirect without a Location header")
                # Every hop must pass the same checks as the registered URL.
                current = check_url(str(current.join(location)), allowlist)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                raise TransientError(f"download failed with HTTP {response.status_code}")
            if not response.is_success:
                raise PermanentError(f"download failed with HTTP {response.status_code}")
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise PermanentError(f"download exceeds the {max_bytes} byte limit")
            partial = dest.with_name(f"{dest.name}.part")
            try:
                result = await _save(response, partial, max_bytes)
                await asyncio.to_thread(partial.replace, dest)
            finally:
                await asyncio.to_thread(_remove, partial)
            return result
    raise PermanentError(f"more than {MAX_REDIRECTS} redirects")


async def download(
    url: str,
    dest: Path,
    *,
    allowlist: Sequence[str],
    max_bytes: int,
    timeout_s: float = DOWNLOAD_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DownloadResult:
    """Downloads `url` to `dest`; nothing is left behind on failure."""
    async with httpx.AsyncClient(
        transport=transport or GuardedTransport(),
        follow_redirects=False,
        timeout=_TIMEOUTS,
        trust_env=False,
    ) as client:
        try:
            async with asyncio.timeout(timeout_s):
                return await _fetch(client, url, dest, allowlist, max_bytes)
        except UrlRejected as exc:
            raise PermanentError(str(exc)) from None
        except TimeoutError:
            raise TransientError("download timed out") from None
        except httpx.TransportError as exc:
            raise TransientError(f"download failed: {type(exc).__name__}") from None
