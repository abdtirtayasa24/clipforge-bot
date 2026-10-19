import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from clipforge.media.process import ProcessTimeout, run_process
from clipforge.queue.errors import PermanentError, TransientError

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 60.0


class MediaInfo(BaseModel):
    format_names: tuple[str, ...]
    duration_s: float | None
    size_bytes: int | None
    bit_rate: int | None
    video_codec: str | None
    width: int | None
    height: int | None
    audio_codec: str | None
    audio_channels: int | None
    audio_sample_rate: int | None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _first_stream(streams: list[dict[str, Any]], codec_type: str) -> dict[str, Any]:
    for stream in streams:
        cover_art = stream.get("disposition", {}).get("attached_pic")
        if stream.get("codec_type") == codec_type and not cover_art:
            return stream
    return {}


def parse_ffprobe(output: dict[str, Any]) -> MediaInfo:
    media_format = output.get("format", {})
    streams = output.get("streams", [])
    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")
    return MediaInfo(
        format_names=tuple(name for name in media_format.get("format_name", "").split(",") if name),
        duration_s=_number(media_format.get("duration")),
        size_bytes=_integer(media_format.get("size")),
        bit_rate=_integer(media_format.get("bit_rate")),
        video_codec=video.get("codec_name"),
        width=_integer(video.get("width")),
        height=_integer(video.get("height")),
        audio_codec=audio.get("codec_name"),
        audio_channels=_integer(audio.get("channels")),
        audio_sample_rate=_integer(audio.get("sample_rate")),
    )


async def probe(path: Path) -> MediaInfo:
    args = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    try:
        result = await run_process([*args, str(path)], timeout_s=PROBE_TIMEOUT_S)
    except ProcessTimeout as exc:
        raise TransientError(str(exc)) from None
    if result.returncode != 0:
        logger.info("ffprobe failed", extra={"stderr": result.stderr.decode(errors="replace")})
        raise PermanentError("unreadable or corrupt media file")
    try:
        output = json.loads(result.stdout)
    except ValueError:
        raise PermanentError("unreadable or corrupt media file") from None
    return parse_ffprobe(output)
