"""Frame sampling as a durable step of the analyze stage.

ADR-006's job stages have no frames stage, so the analyze stage calls this step before its
first AI call; the frame index is its checksum-verified artifact like any stage output.
"""

import asyncio
import logging
from pathlib import Path

from pydantic import ValidationError

from clipforge.ai.types import Frame
from clipforge.media.frames import INTERVAL_S, MIN_GAP_S, FrameIndex, sample_frames
from clipforge.pipeline.stage import verified_artifact, write_artifact

logger = logging.getLogger(__name__)

FRAMES_DIR = "frames"
INDEX_NAME = "index.json"


def read_index(path: Path) -> FrameIndex | None:
    try:
        return FrameIndex.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


async def _reusable(index: FrameIndex, frames_dir: Path, source_sha256: str) -> bool:
    if (index.source_sha256, index.interval_s, index.min_gap_s) != (
        source_sha256,
        INTERVAL_S,
        MIN_GAP_S,
    ):
        return False
    for frame in index.frames:
        if not await verified_artifact(frames_dir / frame.file, frame.sha256):
            return False
    return True


async def ensure_frames(workspace: Path, source: Path, source_sha256: str) -> list[Frame]:
    """Sampled frames of `source`, reusing a verified earlier sampling."""
    frames_dir = workspace / FRAMES_DIR
    index_path = frames_dir / INDEX_NAME
    index = await asyncio.to_thread(read_index, index_path)
    if index is None or not await _reusable(index, frames_dir, source_sha256):
        await asyncio.to_thread(frames_dir.mkdir, mode=0o700, exist_ok=True)
        index = await sample_frames(source, frames_dir, source_sha256=source_sha256)
        await write_artifact(index_path, index.model_dump_json())
        logger.info("frames sampled", extra={"frames": len(index.frames)})
    return [Frame(time_s=frame.time_s, path=frames_dir / frame.file) for frame in index.frames]
