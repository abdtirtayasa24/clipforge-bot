"""One 720x1280 H.264/AAC clip with burned subtitles (ADR-013)."""

import asyncio
import logging
from pathlib import Path

from clipforge.media.probe import MediaInfo, probe
from clipforge.media.process import ProcessTimeout, run_process
from clipforge.queue.errors import TransientError

logger = logging.getLogger(__name__)

WIDTH = 720
HEIGHT = 1280
DURATION_TOLERANCE_S = 0.5
RENDER_TIMEOUT_S = 1800.0
STDERR_LOG_CHARS = 2000


def render_args(
    source: Path, dest: Path, subtitles_name: str, start_s: float, end_s: float
) -> list[str]:
    """`-ss` before `-i` seeks fast and, since the clip is re-encoded, still frame-accurately;
    the output then starts at 0 s, which is what the re-based subtitles expect."""
    filters = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1,subtitles=filename={subtitles_name}"
    )
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y",
        "-ss", f"{start_s:.3f}", "-i", str(source), "-t", f"{end_s - start_s:.3f}",
        "-map", "0:v:0", "-map", "0:a:0", "-sn", "-dn", "-vf", filters,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-threads", "2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        str(dest),
    ]  # fmt: skip


def verify_clip(info: MediaInfo, expected_duration_s: float) -> None:
    problems = []
    if (info.width, info.height) != (WIDTH, HEIGHT):
        problems.append(f"size {info.width}x{info.height}")
    if (info.video_codec, info.audio_codec) != ("h264", "aac"):
        problems.append(f"codecs {info.video_codec}/{info.audio_codec}")
    if info.duration_s is None or abs(info.duration_s - expected_duration_s) > DURATION_TOLERANCE_S:
        problems.append(f"duration {info.duration_s} s, expected {expected_duration_s:.3f} s")
    if problems:
        raise TransientError(f"rendered clip is not compliant: {', '.join(problems)}")


async def render_clip(
    source: Path, dest: Path, subtitles: Path, start_s: float, end_s: float
) -> MediaInfo:
    """Renders to `dest` and verifies it with ffprobe; a failed render leaves no `dest`.

    ffmpeg runs in the subtitles' directory so the subtitles filter gets a bare file name:
    absolute paths would need filtergraph escaping (drive letters, spaces).
    """
    source, dest = await asyncio.to_thread(lambda: (source.absolute(), dest.absolute()))
    args = render_args(source, dest, subtitles.name, start_s, end_s)
    try:
        result = await run_process(args, timeout_s=RENDER_TIMEOUT_S, cwd=subtitles.parent)
    except ProcessTimeout as exc:
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        raise TransientError(str(exc)) from None
    if result.returncode != 0:
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        stderr = result.stderr.decode(errors="replace")[-STDERR_LOG_CHARS:]
        logger.warning("clip render failed", extra={"stderr": stderr})
        raise TransientError("ffmpeg could not render the clip")
    info = await probe(dest)
    try:
        verify_clip(info, end_s - start_s)
    except TransientError:
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        raise
    return info
