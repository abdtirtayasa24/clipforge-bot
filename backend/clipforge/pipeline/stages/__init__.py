from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.pipeline.stage import Stage
from clipforge.pipeline.stages.audio import AudioStage
from clipforge.pipeline.stages.ingest import IngestStage


def default_stages(
    sessions: async_sessionmaker[AsyncSession], *, workspace_root: Path
) -> list[Stage]:
    return [IngestStage(sessions, workspace_root=workspace_root), AudioStage(sessions)]
