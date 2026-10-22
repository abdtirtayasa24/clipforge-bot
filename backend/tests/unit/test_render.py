import uuid
from pathlib import Path

import pytest

from clipforge.media.probe import MediaInfo
from clipforge.media.reframe import Shot
from clipforge.media.render import BLURRED_FIT, framing_filter, render_args, verify_clip
from clipforge.pipeline.stages.render import drive_file_name
from clipforge.queue.errors import TransientError


def info(**overrides: object) -> MediaInfo:
    values: dict[str, object] = {
        "format_names": ("mov", "mp4"),
        "duration_s": 30.2,
        "size_bytes": 1000,
        "bit_rate": 1,
        "video_codec": "h264",
        "width": 720,
        "height": 1280,
        "audio_codec": "aac",
        "audio_channels": 2,
        "audio_sample_rate": 44100,
    }
    values.update(overrides)
    return MediaInfo.model_validate(values)


def test_render_args_seek_before_input_and_encode_the_profile() -> None:
    args = render_args(Path("/src/in.mp4"), Path("/out/clip.part.mp4"), "clip.ass", 125.5, 155.25)

    assert args.index("-ss") < args.index("-i")
    assert args[args.index("-ss") + 1] == "125.500"
    assert args[args.index("-t") + 1] == "29.750"
    assert args.index("-t") > args.index("-i")
    filters = args[args.index("-vf") + 1]
    assert "scale=720:1280:force_original_aspect_ratio=increase" in filters
    assert "crop=720:1280" in filters
    assert filters.endswith("subtitles=filename=clip.ass")
    for flag, value in [
        ("-c:v", "libx264"),
        ("-preset", "veryfast"),
        ("-crf", "23"),
        ("-threads", "2"),
        ("-c:a", "aac"),
        ("-b:a", "128k"),
        ("-movflags", "+faststart"),
    ]:
        assert args[args.index(flag) + 1] == value
    assert args[-1] == str(Path("/out/clip.part.mp4"))


def test_without_shots_the_clip_is_centre_cropped() -> None:
    assert framing_filter(None) == (
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"
    )


def test_cropped_shots_move_the_crop_at_each_cut() -> None:
    shots = [Shot(0.0, 4.0, 0.2), Shot(4.0, 9.5, 0.5), Shot(9.5, 20.0, 0.75)]

    assert framing_filter(shots) == (
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280:"
        "x='clip(if(lt(t,4.000),0.2000,if(lt(t,9.500),0.5000,0.7500))*iw-ow/2,0,iw-ow)':y=0"
    )


def test_unframeable_shots_show_the_whole_frame_over_a_blur() -> None:
    filters = framing_filter([Shot(0.0, 20.0, None)])

    assert filters == BLURRED_FIT
    assert "boxblur" in filters
    assert "force_original_aspect_ratio=decrease" in filters
    assert "crop=720:1280:x=" not in filters


def test_mixed_shots_overlay_the_blurred_frame_only_during_unframeable_shots() -> None:
    shots = [
        Shot(0.0, 4.0, 0.2),
        Shot(4.0, 8.0, None),
        Shot(8.0, 12.0, 0.6),
        Shot(12.0, 15.0, None),
    ]

    filters = framing_filter(shots)

    assert filters.startswith("split[crop][fit];[crop]scale=")
    assert "x='clip(if(lt(t,8.000),0.2000,0.6000)*iw-ow/2,0,iw-ow)'" in filters
    assert f"[fit]{BLURRED_FIT}[whole];" in filters
    assert filters.endswith(
        "[cropped][whole]overlay=enable='gte(t,4.000)*lt(t,8.000)+gte(t,12.000)*lt(t,15.000)'"
    )


def test_render_args_frame_the_clip_before_the_subtitles() -> None:
    shots = [Shot(0.0, 30.0, None)]
    args = render_args(Path("/src/in.mp4"), Path("/out/clip.mp4"), "clip.ass", 0.0, 30.0, shots)

    assert args[args.index("-vf") + 1] == f"{BLURRED_FIT},setsar=1,subtitles=filename=clip.ass"


def test_compliant_clip_passes_verification() -> None:
    verify_clip(info(), 30.0)
    verify_clip(info(duration_s=29.5), 30.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"width": 1280, "height": 720},
        {"video_codec": "hevc"},
        {"audio_codec": None},
        {"duration_s": 30.6},
        {"duration_s": None},
    ],
)
def test_non_compliant_clip_fails_verification(overrides: dict[str, object]) -> None:
    with pytest.raises(TransientError, match="rendered clip"):
        verify_clip(info(**overrides), 30.0)


def test_drive_file_name_is_sanitized_and_unique_per_clip() -> None:
    clip_id = uuid.UUID("1a2b3c4d-0000-4000-8000-000000000000")

    assert drive_file_name('AI: masa depan / "kerja"?', clip_id) == (
        "AI masa depan kerja - 1a2b3c4d.mp4"
    )
    assert drive_file_name("  \t", clip_id) == "clip - 1a2b3c4d.mp4"
    assert drive_file_name(None, clip_id) == "clip - 1a2b3c4d.mp4"
    assert len(drive_file_name("x" * 300, clip_id)) <= 100
