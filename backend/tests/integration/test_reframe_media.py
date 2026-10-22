import subprocess
from pathlib import Path

import pytest

from clipforge.ai.types import TranscriptResult
from clipforge.media.frames import parse_frame_times
from clipforge.media.reframe import Shot, detect_faces, plan_reframing, sampling_args
from clipforge.media.render import render_clip
from clipforge.media.subtitles import build_ass
from tests.fixtures.make_media import make_media

pytestmark = pytest.mark.usefixtures("require_ffmpeg")


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)  # noqa: S603, S607


@pytest.fixture(scope="session")
def landscape_cuts(media_dir: Path, require_ffmpeg: None) -> Path:
    """16:9 red 0-7 s, blue 7-15 s, green 15-24 s; no faces anywhere."""
    path = media_dir / "reframe-cuts.mp4"
    colors = [("red", 7), ("blue", 8), ("green", 9)]
    inputs = [
        arg
        for color, seconds in colors
        for arg in ("-f", "lavfi", "-i", f"color=c={color}:s=640x360:d={seconds}:r=25")
    ]
    ffmpeg(
        *inputs,
        "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(path),
    )  # fmt: skip
    return path


async def test_shots_follow_the_scene_cuts_inside_the_clip(
    landscape_cuts: Path, tmp_path: Path
) -> None:
    work_dir = tmp_path / "clip.reframe"

    shots = await plan_reframing(landscape_cuts, work_dir, 2.0, 20.0)

    assert shots is not None
    assert [shot.crop_center for shot in shots] == [None, None, None]
    assert shots[0].start_s == 0
    assert shots[0].end_s == pytest.approx(5.0, abs=0.1)
    assert shots[1].end_s == pytest.approx(13.0, abs=0.1)
    assert shots[2].end_s == pytest.approx(18.0)
    assert not work_dir.exists()


def test_sampling_stops_decoding_at_the_clip_end(landscape_cuts: Path, tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603
        sampling_args(landscape_cuts, tmp_path, 1.0, 5.0), capture_output=True, check=True
    )

    assert parse_frame_times(result.stderr) == [0.0]
    assert len(list(tmp_path.glob("frame_*.jpg"))) == 8


async def test_a_clip_without_cuts_is_one_shot(media_dir: Path, tmp_path: Path) -> None:
    source = make_media(media_dir / "reframe-one-shot.mp4", duration_s=6, size="640x360")

    shots = await plan_reframing(source, tmp_path / "clip.reframe", 1.0, 5.0)

    assert shots == [Shot(0.0, 4.0, None)]


async def test_a_source_no_wider_than_portrait_needs_no_reframing(
    media_dir: Path, tmp_path: Path
) -> None:
    portrait = make_media(media_dir / "reframe-portrait.mp4", duration_s=4, size="360x640")

    assert await plan_reframing(portrait, tmp_path / "clip.reframe", 0.0, 4.0) is None


def test_frames_without_faces_have_no_face_spans(tmp_path: Path) -> None:
    frame = tmp_path / "frame_00001.jpg"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=640x360", "-frames:v", "1", str(frame))

    aspect, faces = detect_faces([frame, frame])

    assert aspect == pytest.approx(16 / 9)
    assert faces == [[], []]


async def test_mixed_shots_render_a_compliant_clip(media_dir: Path, tmp_path: Path) -> None:
    source = make_media(media_dir / "reframe-landscape.mp4", duration_s=12, size="640x360")
    subtitles = tmp_path / "clip.ass"
    subtitles.write_text(build_ass(TranscriptResult(), 0.0, 9.0, "Judul"), encoding="utf-8")
    shots = [Shot(0.0, 3.0, 0.2), Shot(3.0, 6.0, None), Shot(6.0, 9.0, 0.8)]

    info = await render_clip(source, tmp_path / "clip.mp4", subtitles, 0.0, 9.0, shots)

    assert (info.width, info.height) == (720, 1280)
    assert info.duration_s == pytest.approx(9.0, abs=0.5)
