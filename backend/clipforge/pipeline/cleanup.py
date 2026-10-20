"""Workspace disk hygiene: per-job cleanup on completion and a startup GC (spec resource limits).

Clip files whose Drive upload is not verified are never deleted (ADR-014).
"""

import asyncio
import logging
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.models import ClipArtifact, MediaAsset, ProcessingJob
from clipforge.queue.repository import Clock, utc_now

logger = logging.getLogger(__name__)

JOBS_DIR = "jobs"
SOURCES_DIR = "sources"
TERMINAL_STATUSES = ("completed", "completed_no_clips", "failed", "cancelled")
# Small artifacts kept after completion for review and debugging.
KEPT_AFTER_COMPLETION = frozenset({"transcript", "analysis.json"})


def job_workspace(workspace_root: Path, job_id: uuid.UUID) -> Path:
    return workspace_root / JOBS_DIR / str(job_id)


def _remove_except(directory: Path, keep: set[Path], keep_names: frozenset[str]) -> None:
    """Deletes everything under `directory` except `keep` (files) and top-level `keep_names`."""
    if not directory.is_dir():
        return
    for entry in directory.iterdir():
        if entry.name in keep_names:
            continue
        if entry.is_dir() and not entry.is_symlink():
            if any(path.is_relative_to(entry) for path in keep):
                _remove_except(entry, keep, frozenset())
                continue
            shutil.rmtree(entry)
        elif entry not in keep:
            entry.unlink(missing_ok=True)
    if not keep_names and not any(directory.iterdir()):
        directory.rmdir()


def _age(path: Path, now: datetime) -> timedelta:
    return now - datetime.fromtimestamp(path.stat().st_mtime, now.tzinfo)


class WorkspaceCleaner:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        workspace_root: Path,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._root = workspace_root
        self._clock = clock

    async def _unuploaded_clips(self) -> set[Path]:
        async with self._sessions() as session:
            paths = await session.scalars(
                select(ClipArtifact.local_path).where(
                    ClipArtifact.upload_state != "verified",
                    ClipArtifact.local_path.is_not(None),
                )
            )
            return {Path(path) for path in paths if path}

    async def after_completion(self, job_id: uuid.UUID) -> None:
        """Deletes the job's source and stage files; transcript and analysis stay."""
        keep = await self._unuploaded_clips()
        await asyncio.to_thread(
            _remove_except, job_workspace(self._root, job_id), keep, KEPT_AFTER_COMPLETION
        )
        async with self._sessions.begin() as session:
            job = await session.get_one(ProcessingJob, job_id)
            asset = (
                await session.get(MediaAsset, job.media_asset_id) if job.media_asset_id else None
            )
            if asset is None or not asset.local_path:
                return
            source = Path(asset.local_path)
            # Only files the app stored itself; never a path outside the workspace.
            if source.is_relative_to(self._root / SOURCES_DIR):
                await asyncio.to_thread(source.unlink, missing_ok=True)
                await session.execute(
                    update(MediaAsset).where(MediaAsset.id == asset.id).values(state="deleted")
                )
        logger.info("job workspace cleaned", extra={"job_id": str(job_id)})

    async def collect_garbage(self, *, older_than: timedelta) -> list[Path]:
        """Removes workspaces of jobs that ended (or no longer exist) before `older_than`."""
        jobs_dir = self._root / JOBS_DIR
        if not await asyncio.to_thread(jobs_dir.is_dir):
            return []
        now = self._clock()
        candidates: dict[uuid.UUID, Path] = {}
        for entry in await asyncio.to_thread(lambda: list(jobs_dir.iterdir())):
            try:
                candidates[uuid.UUID(entry.name)] = entry
            except ValueError:
                continue
        async with self._sessions() as session:
            rows = await session.execute(
                select(ProcessingJob.id, ProcessingJob.status, ProcessingJob.finished_at).where(
                    ProcessingJob.id.in_(candidates)
                )
            )
            jobs = {job_id: (status, finished_at) for job_id, status, finished_at in rows}
        keep = await self._unuploaded_clips()
        removed = []
        for job_id, directory in candidates.items():
            if job_id in jobs:
                status, finished_at = jobs[job_id]
                expired = (
                    status in TERMINAL_STATUSES
                    and finished_at is not None
                    and now - finished_at > older_than
                )
            else:
                expired = await asyncio.to_thread(_age, directory, now) > older_than
            if expired:
                await asyncio.to_thread(_remove_except, directory, keep, frozenset())
                removed.append(directory)
        if removed:
            logger.info("workspace garbage collected", extra={"directories": len(removed)})
        return removed
