"""Tiny ffmpeg-generated media for tests (ADR-023): `testsrc` video plus a `sine` tone."""

import subprocess
from pathlib import Path


def make_media(
    path: Path,
    *,
    duration_s: float,
    audio: bool = True,
    video: bool = True,
    size: str = "64x64",
    video_codec: str = "libx264",
) -> Path:
    inputs: list[str] = []
    codecs: list[str] = []
    if video:
        inputs += ["-f", "lavfi", "-i", f"testsrc=size={size}:rate=1"]
        codecs += ["-c:v", video_codec, "-pix_fmt", "yuv420p"]
        if video_codec == "libx264":
            codecs += ["-preset", "ultrafast"]
    if audio:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000"]
        codecs += ["-c:a", "aac", "-b:a", "16k", "-ac", "1"]
    subprocess.run(  # noqa: S603 - fixed arguments, test-only
        ["ffmpeg", "-v", "error", "-y", *inputs, "-t", str(duration_s), *codecs, str(path)],  # noqa: S607
        check=True,
    )
    return path


def make_corrupt(path: Path) -> Path:
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\xde\xad\xbe\xef" * 1024)
    return path
