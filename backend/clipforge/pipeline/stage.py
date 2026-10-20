import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from clipforge.checksums import file_sha256

if TYPE_CHECKING:
    from clipforge.pipeline.context import StageContext


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


async def write_artifact(path: Path, text: str) -> None:
    """Writes a stage artifact so readers only ever see a complete file."""
    partial = path.with_name(path.name + ".part")
    await asyncio.to_thread(partial.write_text, text, encoding="utf-8")
    await asyncio.to_thread(partial.replace, path)


async def verified_artifact(path: Path, expected_sha256: str | None) -> bool:
    """True when a stage can reuse `path` instead of redoing its work."""
    if expected_sha256 is None:
        return False
    return await asyncio.to_thread(file_sha256, path) == expected_sha256
