import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from clipforge.pipeline.context import StageContext

_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class StageResult:
    details: dict[str, Any] = field(default_factory=dict)
    no_clips: bool = False


class Stage(Protocol):
    """One idempotent pipeline step (ADR-006).

    `name` is the job stage recorded once the step has finished; `running_name`, when set, is
    recorded while it runs.
    """

    name: str
    running_name: str | None

    async def run(self, ctx: "StageContext") -> StageResult: ...


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


async def verified_artifact(path: Path, expected_sha256: str | None) -> bool:
    """True when a stage can reuse `path` instead of redoing its work."""
    if expected_sha256 is None:
        return False
    return await asyncio.to_thread(_sha256, path) == expected_sha256
