"""Low-resolution keyframes for visual scoring: a fixed interval plus scene changes (T20)."""

import asyncio
import logging
import re
from pathlib import Path

from pydantic import BaseModel

from clipforge.checksums import file_sha256
from clipforge.media.process import ProcessTimeout, run_process
from clipforge.queue.errors import TransientError

logger = logging.getLogger(__name__)

FRAME_WIDTH = 360
INTERVAL_S = 10.0
# Scene changes closer than this to the previous frame are skipped: at most 12 frames a minute.
MIN_GAP_S = 5.0
SCENE_THRESHOLD = 0.3
FRAMES_TIMEOUT_S = 3600.0
# showinfo writes a few hundred bytes of stderr per frame.
MAX_LOG_BYTES = 16 * 1024 * 1024
STDERR_LOG_CHARS = 2000
_PTS_TIME = re.compile(rb"\bn:\s*\d+\b.*?\bpts_time:([0-9.]+)")


class FrameFile(BaseModel):
    time_s: float
    file: str
    sha256: str


class FrameIndex(BaseModel):
    source_sha256: str
    interval_s: float
    min_gap_s: float
    frames: list[FrameFile]


def select_expression(interval_s: float, min_gap_s: float) -> str:
    since_last = "t-prev_selected_t"
    return (
        f"isnan(prev_selected_t)+gte({since_last}\\,{interval_s:g})"
        f"+gt(scene\\,{SCENE_THRESHOLD:g})*gte({since_last}\\,{min_gap_s:g})"
    )


def parse_frame_times(stderr: bytes) -> list[float]:
    return [float(match) for match in _PTS_TIME.findall(stderr)]


async def sample_frames(
    source: Path,
    out_dir: Path,
    *,
    source_sha256: str,
    interval_s: float = INTERVAL_S,
    min_gap_s: float = MIN_GAP_S,
) -> FrameIndex:
    """ffmpeg decodes keyframes only and writes JPEGs; Python just lists the results."""
    for stale in await asyncio.to_thread(lambda: list(out_dir.glob("frame_*.jpg"))):
        await asyncio.to_thread(stale.unlink)
    filters = f"select='{select_expression(interval_s, min_gap_s)}',scale={FRAME_WIDTH}:-2,showinfo"
    args = [
        "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-v", "info",
        "-skip_frame", "nokey", "-i", str(source), "-an", "-sn", "-dn",
        "-vf", filters, "-fps_mode", "vfr", "-q:v", "5", "-threads", "2",
        str(out_dir / "frame_%05d.jpg"),
    ]  # fmt: skip
    try:
        result = await run_process(args, timeout_s=FRAMES_TIMEOUT_S, max_output_bytes=MAX_LOG_BYTES)
    except ProcessTimeout as exc:
        raise TransientError(str(exc)) from None
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[-STDERR_LOG_CHARS:]
        logger.warning("frame sampling failed", extra={"stderr": stderr})
        raise TransientError("ffmpeg could not sample frames")
    files = sorted(await asyncio.to_thread(lambda: list(out_dir.glob("frame_*.jpg"))))
    times = parse_frame_times(result.stderr)
    if len(times) != len(files):
        raise TransientError(f"sampled {len(files)} frames but ffmpeg reported {len(times)}")
    frames = [
        FrameFile(time_s=time_s, file=path.name, sha256=await asyncio.to_thread(file_sha256, path))
        for time_s, path in zip(times, files, strict=True)
    ]
    return FrameIndex(
        source_sha256=source_sha256, interval_s=interval_s, min_gap_s=min_gap_s, frames=frames
    )
