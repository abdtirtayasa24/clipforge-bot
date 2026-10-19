from pathlib import Path

import pytest

from clipforge.ingest.validation import validate_media
from clipforge.media.probe import MediaInfo, probe
from clipforge.queue.errors import PermanentError
from tests.fixtures.make_media import make_corrupt, make_media

pytestmark = pytest.mark.usefixtures("require_ffmpeg")


def file_size(path: Path) -> int:
    return path.stat().st_size


async def test_probe_reads_streams_and_format(valid_source: Path) -> None:
    info = await probe(valid_source)

    assert info.duration_s == pytest.approx(1860, abs=1)
    assert info.size_bytes == file_size(valid_source)
    assert "mp4" in info.format_names
    assert (info.video_codec, info.width, info.height) == ("h264", 64, 64)
    assert (info.audio_codec, info.audio_channels) == ("aac", 1)
    assert info.bit_rate is not None


async def test_valid_source_passes_validation(valid_source: Path) -> None:
    validate_media(await probe(valid_source))


async def test_source_without_audio_is_rejected(media_dir: Path) -> None:
    path = make_media(media_dir / "silent.mp4", duration_s=31 * 60, audio=False)

    with pytest.raises(PermanentError, match="no audio stream"):
        validate_media(await probe(path))


async def test_too_short_source_is_rejected(media_dir: Path) -> None:
    path = make_media(media_dir / "short.mp4", duration_s=5)

    with pytest.raises(PermanentError, match="duration"):
        validate_media(await probe(path))


async def test_corrupt_file_is_rejected_by_probe(media_dir: Path) -> None:
    path = make_corrupt(media_dir / "corrupt.mp4")

    with pytest.raises(PermanentError, match="unreadable"):
        await probe(path)


async def test_missing_file_is_rejected_by_probe(media_dir: Path) -> None:
    with pytest.raises(PermanentError, match="unreadable"):
        await probe(media_dir / "missing.mp4")


def info(**overrides: object) -> MediaInfo:
    values: dict[str, object] = {
        "format_names": ("mov", "mp4", "m4a", "3gp", "3g2", "mj2"),
        "duration_s": 3600.0,
        "size_bytes": 500_000_000,
        "bit_rate": 1_100_000,
        "video_codec": "h264",
        "width": 1280,
        "height": 720,
        "audio_codec": "aac",
        "audio_channels": 2,
        "audio_sample_rate": 44100,
    }
    return MediaInfo.model_validate(values | overrides)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"format_names": ("avi",)}, "container"),
        ({"video_codec": None, "width": None, "height": None}, "no video stream"),
        ({"video_codec": "mpeg2video"}, "video codec"),
        ({"audio_codec": "pcm_s16le"}, "audio codec"),
        ({"duration_s": 7201.0}, "duration"),
        ({"width": 7680, "height": 4320}, "resolution"),
        ({"bit_rate": 80_000_000}, "bitrate"),
    ],
)
def test_validation_rejects_unsupported_media(overrides: dict[str, object], reason: str) -> None:
    with pytest.raises(PermanentError, match=reason):
        validate_media(info(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"duration_s": 1800.0},
        {"duration_s": 7200.0},
        {"format_names": ("matroska", "webm"), "video_codec": "vp9", "audio_codec": "opus"},
        {"width": 1080, "height": 1920},
    ],
)
def test_validation_accepts_supported_media(overrides: dict[str, object]) -> None:
    validate_media(info(**overrides))
