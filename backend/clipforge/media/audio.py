"""Mono 16 kHz audio chunks for transcription (ADR-012)."""

import asyncio
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from clipforge.media.process import ProcessTimeout, run_process
from clipforge.queue.errors import TransientError

logger = logging.getLogger(__name__)

MAX_CHUNK_MINUTES = 28
CHUNK_OVERLAP_S = 2.0
SAMPLE_RATE_HZ = 16000
AUDIO_BITRATE = "32k"
CHUNK_TIMEOUT_S = 600.0
STDERR_LOG_CHARS = 2000


@dataclass(frozen=True)
class ChunkPlan:
    index: int
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class AudioChunk(BaseModel):
    index: int
    file: str
    start_s: float
    end_s: float
    sha256: str
    size_bytes: int


class AudioManifest(BaseModel):
    """Chunk list of one source; `start_s`/`end_s` are absolute source times."""

    source_sha256: str
    duration_s: float
    chunk_s: float
    overlap_s: float
    chunks: list[AudioChunk]


def plan_chunks(
    duration_s: float, *, chunk_s: float, overlap_s: float = CHUNK_OVERLAP_S
) -> list[ChunkPlan]:
    """Chunk `i` starts at `i * chunk_s` and runs `overlap_s` into the next one."""
    if duration_s <= 0:
        raise ValueError("media duration must be positive")
    if not overlap_s < chunk_s <= MAX_CHUNK_MINUTES * 60:
        raise ValueError(
            f"chunk length must be above {overlap_s:g} s and ≤ {MAX_CHUNK_MINUTES} min"
        )
    # A tail shorter than the overlap is already inside the previous chunk.
    count = max(1, math.ceil((duration_s - overlap_s) / chunk_s))
    return [
        ChunkPlan(i, i * chunk_s, min(duration_s, (i + 1) * chunk_s + overlap_s))
        for i in range(count)
    ]


async def extract_chunk(source: Path, dest: Path, plan: ChunkPlan) -> None:
    """Encodes one chunk as mono 16 kHz MP3; `dest` only appears once complete."""
    partial = dest.with_name(dest.name + ".part")
    args = [
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-ss", f"{plan.start_s:.3f}", "-t", f"{plan.duration_s:.3f}", "-i", str(source),
        "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SAMPLE_RATE_HZ),
        "-c:a", "libmp3lame", "-b:a", AUDIO_BITRATE, "-threads", "2",
        "-f", "mp3", str(partial),
    ]  # fmt: skip
    try:
        result = await run_process(args, timeout_s=CHUNK_TIMEOUT_S)
    except ProcessTimeout as exc:
        await asyncio.to_thread(partial.unlink, missing_ok=True)
        raise TransientError(str(exc)) from None
    if result.returncode != 0:
        await asyncio.to_thread(partial.unlink, missing_ok=True)
        stderr = result.stderr.decode(errors="replace")[-STDERR_LOG_CHARS:]
        logger.warning("audio extraction failed", extra={"chunk": plan.index, "stderr": stderr})
        raise TransientError(f"ffmpeg could not extract audio chunk {plan.index}")
    await asyncio.to_thread(partial.replace, dest)
