"""Subject-aware 9:16 framing, decided per shot: a crop around the detected faces, or the
whole frame over a blurred background when they would not fit in one crop (ADR-033)."""

import asyncio
import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2

from clipforge.media.frames import SCENE_THRESHOLD, parse_frame_times
from clipforge.media.process import ProcessTimeout, run_process
from clipforge.queue.errors import TransientError

logger = logging.getLogger(__name__)

# Native OpenCV warnings go straight to stderr and would bypass the JSON logger (ADR-022).
cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)

MODEL_PATH = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"
PORTRAIT_ASPECT = 9 / 16
SAMPLE_FPS = 2.0
SAMPLE_WIDTH = 640
MIN_FACE_SCORE = 0.8
NMS_THRESHOLD = 0.3
MAX_FACES = 20
MIN_SHOT_S = 1.0
REFRAME_TIMEOUT_S = 900.0
STDERR_LOG_CHARS = 2000


@dataclass(frozen=True)
class FaceSpan:
    """A face's horizontal extent as fractions of the frame width."""

    left: float
    right: float


@dataclass(frozen=True)
class Shot:
    start_s: float
    end_s: float
    # Fraction of the frame width; None shows the whole frame over a blurred background.
    crop_center: float | None


def shot_spans(
    cut_times: Sequence[float], duration_s: float, min_shot_s: float = MIN_SHOT_S
) -> list[tuple[float, float]]:
    """Shots between scene cuts; a cut closer than `min_shot_s` to the previous cut or to the
    clip's end is ignored, so a flash frame never gets its own framing."""
    starts = [0.0]
    for cut in sorted(cut_times):
        if cut - starts[-1] >= min_shot_s and duration_s - cut >= min_shot_s:
            starts.append(cut)
    return list(zip(starts, [*starts[1:], duration_s], strict=True))


def crop_center(faces: Sequence[FaceSpan], crop_width: float) -> float | None:
    """Centre of a `crop_width`-wide crop that holds every face, or None when none can."""
    if not faces:
        return None
    left = min(face.left for face in faces)
    right = max(face.right for face in faces)
    if right - left > crop_width:
        return None
    half = crop_width / 2
    return min(max((left + right) / 2, half), 1 - half)


def plan_shots(
    spans: Sequence[tuple[float, float]],
    samples: Sequence[tuple[float, Sequence[FaceSpan]]],
    crop_width: float,
) -> list[Shot]:
    """Every face seen anywhere in a shot must fit, so one crop holds for the whole shot."""
    return [
        Shot(
            start_s,
            end_s,
            crop_center(
                [face for time_s, faces in samples if start_s <= time_s < end_s for face in faces],
                crop_width,
            ),
        )
        for start_s, end_s in spans
    ]


def sampling_args(source: Path, out_dir: Path, start_s: float, end_s: float) -> list[str]:
    """One decode of the clip range: frames for face detection and showinfo for scene cuts.

    The first frame always passes the scene select: ffmpeg fails an output that gets no frames,
    which a clip without cuts would otherwise be. `-t` limits the input, not one output: the
    cut output would otherwise decode to the source's end while ffmpeg 6.1 queues frames for
    the finished frame output until the worker runs out of memory.
    """
    filters = (
        f"[0:v]scale={SAMPLE_WIDTH}:-2,split[sampled][scenes];"
        f"[sampled]fps={SAMPLE_FPS:g}[frames];"
        f"[scenes]select='eq(n,0)+gt(scene,{SCENE_THRESHOLD:g})',showinfo[cuts]"
    )
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-v", "info", "-y",
        "-ss", f"{start_s:.3f}", "-t", f"{end_s - start_s:.3f}", "-i", str(source),
        "-an", "-sn", "-dn", "-filter_complex", filters,
        "-map", "[frames]", "-q:v", "5", "-threads", "2", str(out_dir / "frame_%05d.jpg"),
        "-map", "[cuts]", "-f", "null", "-",
    ]  # fmt: skip


def detect_faces(frames: Sequence[Path]) -> tuple[float, list[list[FaceSpan]]]:
    """Blocking. The frames' width-to-height ratio and the faces found in each frame."""
    detector: cv2.FaceDetectorYN | None = None
    aspect = 0.0
    found: list[list[FaceSpan]] = []
    for path in frames:
        image = cv2.imread(str(path))
        if image is None:
            raise TransientError(f"could not read the sampled frame {path.name}")
        height, width = image.shape[:2]
        if detector is None:
            aspect = width / height
            detector = cv2.FaceDetectorYN.create(
                str(MODEL_PATH), "", (width, height), MIN_FACE_SCORE, NMS_THRESHOLD, MAX_FACES
            )
        _, faces = detector.detect(image)
        found.append(
            []
            if faces is None
            else [
                FaceSpan(max(0.0, float(x)) / width, min(float(width), float(x + w)) / width)
                for x, w in faces[:, [0, 2]]
            ]
        )
    return aspect, found


async def plan_reframing(
    source: Path, work_dir: Path, start_s: float, end_s: float
) -> list[Shot] | None:
    """The clip's shots and their framing; None when the source is no wider than 9:16, so the
    plain centre crop already shows all of it."""
    await asyncio.to_thread(shutil.rmtree, work_dir, ignore_errors=True)
    await asyncio.to_thread(work_dir.mkdir, mode=0o700, parents=True)
    try:
        try:
            result = await run_process(
                sampling_args(source, work_dir, start_s, end_s), timeout_s=REFRAME_TIMEOUT_S
            )
        except ProcessTimeout as exc:
            raise TransientError(str(exc)) from None
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[-STDERR_LOG_CHARS:]
            logger.warning("reframe sampling failed", extra={"stderr": stderr})
            raise TransientError("ffmpeg could not sample the clip for reframing")
        frames = sorted(await asyncio.to_thread(lambda: list(work_dir.glob("frame_*.jpg"))))
        if not frames:
            raise TransientError("ffmpeg sampled no frames for reframing")
        aspect, faces = await asyncio.to_thread(detect_faces, frames)
    finally:
        await asyncio.to_thread(shutil.rmtree, work_dir, ignore_errors=True)
    crop_width = PORTRAIT_ASPECT / aspect
    if crop_width >= 1:
        return None
    samples = [(index / SAMPLE_FPS, found) for index, found in enumerate(faces)]
    spans = shot_spans(parse_frame_times(result.stderr), end_s - start_s)
    return plan_shots(spans, samples, crop_width)
