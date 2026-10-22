"""One 720x1280 H.264/AAC clip with burned subtitles (ADR-013, ADR-033)."""

import asyncio
import logging
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

from clipforge.media.probe import MediaInfo, probe
from clipforge.media.process import ProcessTimeout, run_process
from clipforge.media.reframe import Shot
from clipforge.queue.errors import TransientError

logger = logging.getLogger(__name__)

WIDTH = 720
HEIGHT = 1280
DURATION_TOLERANCE_S = 0.5
RENDER_TIMEOUT_S = 1800.0
STDERR_LOG_CHARS = 2000

FILL = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase"
# The background is blurred at quarter size, which costs a fraction of a full-size blur.
BLURRED_FIT = (
    "split[background][foreground];"
    f"[background]scale={WIDTH // 4}:{HEIGHT // 4}:force_original_aspect_ratio=increase,"
    f"crop={WIDTH // 4}:{HEIGHT // 4},boxblur=8,scale={WIDTH}:{HEIGHT}[blurred];"
    f"[foreground]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease[fitted];"
    "[blurred][fitted]overlay=(W-w)/2:(H-h)/2"
)


def tracked_crop(shots: Sequence[Shot]) -> str:
    """A crop whose x follows each cropped shot's centre; crop evaluates x on every frame."""
    cropped = [shot for shot in shots if shot.crop_center is not None]
    center = f"{cropped[-1].crop_center:.4f}"
    for shot, following in reversed(list(pairwise(cropped))):
        center = f"if(lt(t,{following.start_s:.3f}),{shot.crop_center:.4f},{center})"
    return f"{FILL},crop={WIDTH}:{HEIGHT}:x='clip({center}*iw-ow/2,0,iw-ow)':y=0"


def framing_filter(shots: Sequence[Shot] | None) -> str:
    """The plain centre crop without shots; otherwise each shot is cropped or shown whole."""
    if shots is None:
        return f"{FILL},crop={WIDTH}:{HEIGHT}"
    fitted = [shot for shot in shots if shot.crop_center is None]
    if not fitted:
        return tracked_crop(shots)
    if len(fitted) == len(shots):
        return BLURRED_FIT
    fit_times = "+".join(f"gte(t,{s.start_s:.3f})*lt(t,{s.end_s:.3f})" for s in fitted)
    return (
        f"split[crop][fit];[crop]{tracked_crop(shots)}[cropped];[fit]{BLURRED_FIT}[whole];"
        f"[cropped][whole]overlay=enable='{fit_times}'"
    )


def render_args(
    source: Path,
    dest: Path,
    subtitles_name: str,
    start_s: float,
    end_s: float,
    shots: Sequence[Shot] | None = None,
) -> list[str]:
    """`-ss` before `-i` seeks fast and, since the clip is re-encoded, still frame-accurately;
    the output then starts at 0 s, which is what the re-based subtitles and shots expect."""
    filters = f"{framing_filter(shots)},setsar=1,subtitles=filename={subtitles_name}"
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
    source: Path,
    dest: Path,
    subtitles: Path,
    start_s: float,
    end_s: float,
    shots: Sequence[Shot] | None = None,
) -> MediaInfo:
    """Renders to `dest` and verifies it with ffprobe; a failed render leaves no `dest`.

    ffmpeg runs in the subtitles' directory so the subtitles filter gets a bare file name:
    absolute paths would need filtergraph escaping (drive letters, spaces).
    """
    source, dest = await asyncio.to_thread(lambda: (source.absolute(), dest.absolute()))
    args = render_args(source, dest, subtitles.name, start_s, end_s, shots)
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
