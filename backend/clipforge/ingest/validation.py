from clipforge.media.probe import MediaInfo
from clipforge.queue.errors import PermanentError

MIN_DURATION_S = 30 * 60
MAX_DURATION_S = 120 * 60
ALLOWED_CONTAINERS = frozenset({"mov", "mp4", "matroska", "webm"})
ALLOWED_VIDEO_CODECS = frozenset({"h264", "hevc", "vp9", "av1"})
ALLOWED_AUDIO_CODECS = frozenset({"aac", "mp3", "opus", "vorbis", "ac3", "eac3"})
MAX_LONG_SIDE_PX = 3840
MAX_SHORT_SIDE_PX = 2160
MAX_BIT_RATE = 50_000_000


def validate_media(info: MediaInfo) -> None:
    """Raises PermanentError naming the first reason the source cannot be processed."""
    if not ALLOWED_CONTAINERS.intersection(info.format_names):
        raise PermanentError(f"unsupported container: {','.join(info.format_names) or 'unknown'}")
    if info.video_codec is None:
        raise PermanentError("no video stream")
    if info.video_codec not in ALLOWED_VIDEO_CODECS:
        raise PermanentError(f"unsupported video codec: {info.video_codec}")
    if info.audio_codec is None:
        raise PermanentError("no audio stream")
    if info.audio_codec not in ALLOWED_AUDIO_CODECS:
        raise PermanentError(f"unsupported audio codec: {info.audio_codec}")
    if info.duration_s is None or not MIN_DURATION_S <= info.duration_s <= MAX_DURATION_S:
        raise PermanentError(
            f"duration {info.duration_s or 0:.0f} s is outside {MIN_DURATION_S}-{MAX_DURATION_S} s"
        )
    if not info.width or not info.height:
        raise PermanentError("unknown video resolution")
    long_side, short_side = max(info.width, info.height), min(info.width, info.height)
    if long_side > MAX_LONG_SIDE_PX or short_side > MAX_SHORT_SIDE_PX:
        raise PermanentError(f"resolution {info.width}x{info.height} exceeds 4K")
    if info.bit_rate is not None and info.bit_rate > MAX_BIT_RATE:
        raise PermanentError(f"bitrate {info.bit_rate} bit/s exceeds {MAX_BIT_RATE}")
