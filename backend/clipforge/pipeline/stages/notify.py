"""Admin notifications. Sending is best effort: a failure is logged and never changes a job."""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.app_settings import get_setting
from clipforge.db.models import AdminUser, Clip, ProcessingJob, Video
from clipforge.db.models import ClipCandidate as ClipCandidateRow
from clipforge.discovery.service import DiscoveryReport
from clipforge.logging import redact
from clipforge.notify.telegram import TelegramClient
from clipforge.queue.repository import JobQueue

logger = logging.getLogger(__name__)

NOTIFICATIONS_KEY = "telegram_notifications_enabled"
REASON_MAX_CHARS = 300
DRIVE_DISCONNECTED = (
    "ClipForge: Google Drive is disconnected.\n"
    "Reconnect it in the app settings; the upload resumes when the job is retried."
)


@dataclass(frozen=True)
class ReadyClip:
    title: str
    score: int


def _cost_lines(costs: Mapping[str, Decimal]) -> list[str]:
    totals = costs or {"USD": Decimal(0)}
    return [f"Estimated AI cost: {total:.4f} {currency}" for currency, total in totals.items()]


def clips_ready_message(
    video_title: str, clips: list[ReadyClip], costs: Mapping[str, Decimal]
) -> str:
    lines = [f"ClipForge: {len(clips)} clips ready for review", f"Video: {video_title}"]
    lines += [f"{rank}. {clip.title} (score {clip.score})" for rank, clip in enumerate(clips, 1)]
    return "\n".join(lines + _cost_lines(costs))


def job_failed_message(video_title: str, stage: str | None, reason: str | None) -> str:
    reason_text = redact(reason or "unknown error")[:REASON_MAX_CHARS]
    return (
        f"ClipForge: job failed\nVideo: {video_title}\nStage: {stage or 'unknown'}\n"
        f"Reason: {reason_text}"
    )


def discovery_message(report: DiscoveryReport) -> str:
    sources = f"Sources run: {report.sources_run}"
    if report.failed_sources:
        failed = ", ".join(report.failed_sources)
        sources += f" ({len(report.failed_sources)} failed: {failed})"
    return "\n".join(
        [
            "ClipForge: discovery finished",
            sources,
            f"New videos: {report.new_videos}, updated: {report.updated_videos}",
        ]
    )


class Notifier:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], telegram: TelegramClient | None
    ) -> None:
        self._sessions = sessions
        self._telegram = telegram

    async def clips_ready(self, job_id: uuid.UUID) -> None:
        try:
            async with self._sessions() as session:
                video_title = await self._video_title(session, job_id)
                rows = await session.execute(
                    select(Clip.title_override, Clip.title, ClipCandidateRow.total_score)
                    .join(ClipCandidateRow, ClipCandidateRow.id == Clip.candidate_id)
                    .where(Clip.job_id == job_id)
                    .order_by(ClipCandidateRow.rank, Clip.created_at)
                )
                clips = [
                    ReadyClip(override or title or "Untitled clip", round(score))
                    for override, title, score in rows
                ]
            costs = await JobQueue(self._sessions).estimated_costs(job_id)
        except Exception:
            logger.warning("clips-ready notification skipped", exc_info=True)
            return
        await self._send(clips_ready_message(video_title, clips, costs))

    async def job_failed(self, job_id: uuid.UUID) -> None:
        try:
            async with self._sessions() as session:
                video_title = await self._video_title(session, job_id)
                job = await session.get_one(ProcessingJob, job_id)
                text = job_failed_message(video_title, job.stage, job.last_error)
        except Exception:
            logger.warning("job-failed notification skipped", exc_info=True)
            return
        await self._send(text)

    async def drive_disconnected(self) -> None:
        await self._send(DRIVE_DISCONNECTED)

    async def discovery_summary(self, report: DiscoveryReport) -> None:
        await self._send(discovery_message(report))

    @staticmethod
    async def _video_title(session: AsyncSession, job_id: uuid.UUID) -> str:
        title = await session.scalar(
            select(Video.title)
            .join(ProcessingJob, ProcessingJob.video_id == Video.id)
            .where(ProcessingJob.id == job_id)
        )
        return title or "unknown video"

    async def _send(self, text: str) -> None:
        if self._telegram is None:
            return
        try:
            async with self._sessions() as session:
                if await get_setting(session, NOTIFICATIONS_KEY) is False:
                    return
                chat_ids = list(
                    await session.scalars(
                        select(AdminUser.telegram_user_id).where(AdminUser.active)
                    )
                )
            for chat_id in chat_ids:
                await self._telegram.send_message(chat_id, text)
        except Exception as exc:
            logger.warning("telegram notification failed", extra={"reason": str(exc)})
