import subprocess
from itertools import pairwise
from pathlib import Path

import pytest

from clipforge.checksums import file_sha256
from clipforge.media.frames import MIN_GAP_S, parse_frame_times, sample_frames
from clipforge.media.probe import probe
from clipforge.pipeline.stages.frames import FRAMES_DIR, INDEX_NAME, ensure_frames, read_index

pytestmark = pytest.mark.usefixtures("require_ffmpeg")

CUTS_S = (7.0, 15.0)


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)  # noqa: S603, S607


@pytest.fixture(scope="session")
def scene_cuts(media_dir: Path, require_ffmpeg: None) -> Path:
    """Red 0-7 s, blue 7-15 s, green 15-24 s; a keyframe every 2 s."""
    path = media_dir / "frames-cuts.mp4"
    colors = [("red", 7), ("blue", 8), ("green", 9)]
    inputs = [
        arg
        for color, seconds in colors
        for arg in ("-f", "lavfi", "-i", f"color=c={color}:s=640x360:d={seconds}:r=5")
    ]
    ffmpeg(
        *inputs,
        "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "10",
        str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def constant_change(media_dir: Path, require_ffmpeg: None) -> Path:
    """Two minutes of random noise: every keyframe looks like a scene change."""
    path = media_dir / "frames-noise.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "nullsrc=s=64x64:r=5:d=120,geq=random(1)*255:128:128",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "5",
        str(path),
    )  # fmt: skip
    return path


async def test_frames_are_sampled_near_scene_cuts(scene_cuts: Path, tmp_path: Path) -> None:
    index = await sample_frames(scene_cuts, tmp_path, source_sha256="x", interval_s=60)

    times = [frame.time_s for frame in index.frames]
    assert times[0] == 0
    assert len(times) == 1 + len(CUTS_S)
    for cut, sampled in zip(CUTS_S, times[1:], strict=True):
        assert cut <= sampled <= cut + 2.5
    first = tmp_path / index.frames[0].file
    assert first.read_bytes()[:2] == b"\xff\xd8"
    assert index.frames[0].sha256 == file_sha256(first)


async def test_interval_frames_fill_scenes_without_cuts(scene_cuts: Path, tmp_path: Path) -> None:
    index = await sample_frames(scene_cuts, tmp_path, source_sha256="x", interval_s=4, min_gap_s=60)

    times = [frame.time_s for frame in index.frames]
    assert times == sorted(times)
    assert all(4 <= later - earlier <= 6 for earlier, later in pairwise(times))


async def test_frame_count_is_bounded(constant_change: Path, tmp_path: Path) -> None:
    index = await sample_frames(constant_change, tmp_path, source_sha256="x")

    times = [frame.time_s for frame in index.frames]
    assert len(times) <= 120 / MIN_GAP_S + 1
    assert all(later - earlier >= MIN_GAP_S - 0.01 for earlier, later in pairwise(times))


async def test_frames_are_scaled_down(scene_cuts: Path, tmp_path: Path) -> None:
    index = await sample_frames(scene_cuts, tmp_path, source_sha256="x", interval_s=60)

    info = await probe(tmp_path / index.frames[0].file)
    assert (info.width, info.height) == (360, 202)


async def test_sampling_is_reused_until_a_frame_changes(scene_cuts: Path, tmp_path: Path) -> None:
    sha = file_sha256(scene_cuts)
    frames = await ensure_frames(tmp_path, scene_cuts, sha)
    index_path = tmp_path / FRAMES_DIR / INDEX_NAME
    written = index_path.stat().st_mtime_ns

    again = await ensure_frames(tmp_path, scene_cuts, sha)
    assert again == frames
    assert index_path.stat().st_mtime_ns == written

    frames[0].path.write_bytes(b"corrupt")
    repaired = await ensure_frames(tmp_path, scene_cuts, sha)
    index = read_index(index_path)
    assert index is not None
    assert [f.time_s for f in repaired] == [f.time_s for f in frames]
    assert file_sha256(repaired[0].path) == index.frames[0].sha256


def test_frame_times_come_from_showinfo() -> None:
    stderr = (
        b"[Parsed_showinfo_2 @ 0x1] config in time_base: 1/5\n"
        b"[Parsed_showinfo_2 @ 0x1] n:   0 pts:      0 pts_time:0       duration:1\n"
        b"[Parsed_showinfo_2 @ 0x1]   side data - ...\n"
        b"[Parsed_showinfo_2 @ 0x1] n:   1 pts:     40 pts_time:8       duration:1\n"
        b"[Parsed_showinfo_2 @ 0x1] n:   2 pts:     82 pts_time:16.4    duration:1\n"
    )

    assert parse_frame_times(stderr) == [0.0, 8.0, 16.4]
