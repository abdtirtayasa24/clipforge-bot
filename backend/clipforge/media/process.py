import asyncio
import contextlib
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

MAX_OUTPUT_BYTES = 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class ProcessTimeout(Exception):
    pass


async def _read_capped(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    """Keeps the first `limit` bytes and drains the rest so the child never blocks on a pipe."""
    kept = bytearray()
    if stream is None:
        return b""
    while chunk := await stream.read(_READ_CHUNK_BYTES):
        if len(kept) < limit:
            kept += chunk[: limit - len(kept)]
    return bytes(kept)


async def run_process(
    args: Sequence[str], *, timeout_s: float, max_output_bytes: int = MAX_OUTPUT_BYTES
) -> ProcessResult:
    """Runs a media tool without blocking the event loop (ADR-008); output is size-limited."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except NotImplementedError:
        # Windows development only: psycopg needs the selector loop, which has no subprocesses.
        return await asyncio.to_thread(_run_blocking, args, timeout_s, max_output_bytes)
    try:
        async with asyncio.timeout(timeout_s):
            stdout, stderr, returncode = await asyncio.gather(
                _read_capped(process.stdout, max_output_bytes),
                _read_capped(process.stderr, max_output_bytes),
                process.wait(),
            )
    except BaseException as exc:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        if isinstance(exc, TimeoutError):
            raise ProcessTimeout(f"{args[0]} timed out after {timeout_s:g} s") from None
        raise
    return ProcessResult(returncode, stdout, stderr)


def _run_blocking(args: Sequence[str], timeout_s: float, max_output_bytes: int) -> ProcessResult:
    try:
        completed = subprocess.run(  # noqa: S603 - argument list, never a shell
            list(args), capture_output=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        raise ProcessTimeout(f"{args[0]} timed out after {timeout_s:g} s") from None
    return ProcessResult(
        completed.returncode,
        completed.stdout[:max_output_bytes],
        completed.stderr[:max_output_bytes],
    )
