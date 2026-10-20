"""Durable job queue: every job state write goes through this module (ADR-005, ADR-007)."""

import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.models import JobEvent, ProcessingJob, ProviderUsage
from clipforge.logging import redact
from clipforge.queue.backoff import retry_delay
from clipforge.queue.errors import (
    ActiveJobExists,
    Cancelled,
    IdempotencyConflict,
    InvalidJobTransition,
    JobNotFound,
    LeaseLost,
    PermanentError,
    QuotaDeferred,
)

logger = logging.getLogger(__name__)

LEASE_DURATION = timedelta(minutes=5)
ACTIVE_STATUSES = ("awaiting_source", "queued", "running", "retry_scheduled")
LAST_ERROR_MAX_CHARS = 2000
_ONE_ACTIVE_JOB_PER_VIDEO = "processing_jobs_one_active_per_video"
_CLEARED_LEASE = {"lease_token": None, "lease_expires_at": None, "leased_by": None}
_system_random = random.SystemRandom()

Clock = Callable[[], datetime]
SettingsSnapshot = Callable[[AsyncSession], Awaitable[dict[str, Any]]]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Lease:
    job_id: uuid.UUID
    token: uuid.UUID
    version: int
    expires_at: datetime


@dataclass(frozen=True)
class ClaimedJob:
    lease: Lease
    video_id: uuid.UUID
    media_asset_id: uuid.UUID | None
    stage: str
    attempt: int
    max_attempts: int
    cancel_requested: bool
    settings_snapshot: dict[str, Any]


def _error_text(error: BaseException) -> str:
    return redact(str(error) or type(error).__name__)[:LAST_ERROR_MAX_CHARS]


class JobQueue:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        clock: Clock = utc_now,
        jitter: Callable[[], float] = _system_random.random,
        snapshot_settings: SettingsSnapshot | None = None,
    ) -> None:
        """`snapshot_settings` resolves the settings a job keeps for its whole run (ADR-026)."""
        self._sessions = sessions
        self._clock = clock
        self._jitter = jitter
        self._snapshot_settings = snapshot_settings

    async def enqueue(
        self,
        video_id: uuid.UUID,
        idempotency_key: str,
        *,
        media_asset_id: uuid.UUID | None = None,
    ) -> tuple[ProcessingJob, bool]:
        """Returns (job, created); the same key returns the existing job."""
        status = "queued" if media_asset_id else "awaiting_source"
        async with self._sessions.begin() as session:
            try:
                job_id = await session.scalar(
                    insert(ProcessingJob)
                    .values(
                        video_id=video_id,
                        media_asset_id=media_asset_id,
                        idempotency_key=idempotency_key,
                        status=status,
                        stage=status,
                        next_attempt_at=self._clock(),
                    )
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                    .returning(ProcessingJob.id)
                )
            except IntegrityError as exc:
                constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                if constraint == _ONE_ACTIVE_JOB_PER_VIDEO:
                    raise ActiveJobExists(f"video {video_id} already has an active job") from None
                raise
            if job_id is None:
                existing = await session.scalar(
                    select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key)
                )
                if existing is None or existing.video_id != video_id:
                    raise IdempotencyConflict("idempotency key belongs to another video")
                return existing, False
            session.add(
                JobEvent(
                    job_id=job_id, event_type="created", stage=status, status=status, attempt=0
                )
            )
            job = await session.get_one(ProcessingJob, job_id)
        logger.info("job created", extra={"job_id": str(job_id), "status": status})
        return job, True

    async def claim_next(self, worker_id: str) -> ClaimedJob | None:
        now = self._clock()
        eligible = or_(
            and_(
                ProcessingJob.status.in_(("queued", "retry_scheduled")),
                ProcessingJob.next_attempt_at <= now,
            ),
            and_(ProcessingJob.status == "running", ProcessingJob.lease_expires_at < now),
        )
        async with self._sessions.begin() as session:
            while True:
                job = await session.scalar(
                    select(ProcessingJob)
                    .where(eligible)
                    .order_by(ProcessingJob.next_attempt_at, ProcessingJob.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if job is None:
                    return None
                reclaimed = job.status == "running"
                if reclaimed and job.attempts >= job.max_attempts:
                    self._fail_exhausted(session, job, now)
                    await session.flush()
                    continue
                return await self._lease(session, job, worker_id, now, reclaimed=reclaimed)

    def _fail_exhausted(self, session: AsyncSession, job: ProcessingJob, now: datetime) -> None:
        message = "lease expired on the last attempt"
        job.status = "failed"
        job.last_error = message
        job.finished_at = now
        job.lease_token = job.lease_expires_at = job.leased_by = None
        session.add(self._event(job, "failed", message=message))
        logger.warning("job failed", extra={"job_id": str(job.id), "reason": message})

    async def _lease(
        self,
        session: AsyncSession,
        job: ProcessingJob,
        worker_id: str,
        now: datetime,
        *,
        reclaimed: bool,
    ) -> ClaimedJob:
        if self._snapshot_settings is not None and not job.settings_snapshot:
            job.settings_snapshot = await self._snapshot_settings(session)
        lease = Lease(job.id, uuid.uuid4(), job.lease_version + 1, now + LEASE_DURATION)
        job.status = "running"
        job.attempts += 1
        job.lease_token = lease.token
        job.lease_version = lease.version
        job.lease_expires_at = lease.expires_at
        job.leased_by = worker_id
        job.started_at = job.started_at or now
        event_type = "reclaimed" if reclaimed else "claimed"
        session.add(self._event(job, event_type, details={"worker_id": worker_id}))
        await session.flush()
        logger.info(
            f"job {event_type}",
            extra={"job_id": str(job.id), "attempt": job.attempts, "worker_id": worker_id},
        )
        return ClaimedJob(
            lease=lease,
            video_id=job.video_id,
            media_asset_id=job.media_asset_id,
            stage=job.stage,
            attempt=job.attempts,
            max_attempts=job.max_attempts,
            cancel_requested=job.cancel_requested,
            settings_snapshot=job.settings_snapshot,
        )

    @staticmethod
    def _event(
        job: ProcessingJob,
        event_type: str,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> JobEvent:
        return JobEvent(
            job_id=job.id,
            event_type=event_type,
            stage=job.stage,
            status=job.status,
            attempt=job.attempts,
            lease_version=job.lease_version,
            message=message,
            details=details or {},
        )

    async def _fenced(
        self, session: AsyncSession, lease: Lease, values: dict[str, Any]
    ) -> ProcessingJob:
        """Locks the job if `lease` still owns it and applies `values`; raises LeaseLost."""
        job = await session.scalar(
            select(ProcessingJob)
            .where(
                ProcessingJob.id == lease.job_id,
                ProcessingJob.lease_token == lease.token,
                ProcessingJob.lease_version == lease.version,
            )
            .with_for_update()
        )
        if job is None:
            raise LeaseLost(f"lease {lease.version} on job {lease.job_id} is no longer held")
        for name, value in values.items():
            setattr(job, name, value)
        await session.flush()
        return job

    async def _transition(
        self,
        lease: Lease,
        values: dict[str, Any],
        event_type: str,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        async with self._sessions.begin() as session:
            job = await self._fenced(session, lease, values)
            session.add(self._event(job, event_type, message=message, details=details))

    async def estimated_costs(self, job_id: uuid.UUID) -> dict[str, Decimal]:
        """The job's total estimated AI cost per currency: the sum of its usage rows (ADR-024)."""
        async with self._sessions() as session:
            rows = await session.execute(
                select(ProviderUsage.currency, func.sum(ProviderUsage.estimated_cost))
                .where(ProviderUsage.job_id == job_id)
                .group_by(ProviderUsage.currency)
                .order_by(ProviderUsage.currency)
            )
            return {currency: total for currency, total in rows}

    async def cancel_requested(self, job_id: uuid.UUID) -> bool:
        async with self._sessions() as session:
            requested = await session.scalar(
                select(ProcessingJob.cancel_requested).where(ProcessingJob.id == job_id)
            )
        return bool(requested)

    async def fenced_update(self, lease: Lease, **fields: Any) -> None:
        async with self._sessions.begin() as session:
            await self._fenced(session, lease, fields)

    async def renew(self, lease: Lease) -> Lease:
        expires_at = self._clock() + LEASE_DURATION
        async with self._sessions.begin() as session:
            await self._fenced(session, lease, {"lease_expires_at": expires_at})
        return Lease(lease.job_id, lease.token, lease.version, expires_at)

    async def set_stage(
        self, lease: Lease, stage: str, *, details: dict[str, Any] | None = None
    ) -> None:
        await self._transition(lease, {"stage": stage}, "stage_changed", details=details)

    async def complete(self, lease: Lease, *, no_clips: bool = False) -> None:
        status = "completed_no_clips" if no_clips else "completed"
        values = {"status": status, "stage": status, "finished_at": self._clock()}
        await self._transition(lease, values | _CLEARED_LEASE, status)
        logger.info("job finished", extra={"job_id": str(lease.job_id), "status": status})

    # Failed and cancelled jobs keep their stage so the timeline shows where they stopped and
    # a manual retry resumes from there.
    async def fail(self, lease: Lease, error: BaseException) -> None:
        message = _error_text(error)
        values = {"status": "failed", "last_error": message, "finished_at": self._clock()}
        await self._transition(lease, values | _CLEARED_LEASE, "failed", message=message)
        logger.warning("job failed", extra={"job_id": str(lease.job_id), "reason": message})

    async def cancel(self, lease: Lease) -> None:
        values = {"status": "cancelled", "finished_at": self._clock()}
        await self._transition(lease, values | _CLEARED_LEASE, "cancelled")
        logger.info("job cancelled", extra={"job_id": str(lease.job_id)})

    async def schedule_retry(self, lease: Lease, error: BaseException) -> str:
        """Schedules the next attempt, or fails the job once its attempts are used up."""
        message = _error_text(error)
        now = self._clock()
        async with self._sessions.begin() as session:
            job = await self._fenced(session, lease, {"last_error": message})
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.finished_at = now
                event_type, details = "failed", {}
            else:
                delay = retry_delay(job.attempts, jitter=self._jitter())
                job.status = "retry_scheduled"
                job.next_attempt_at = now + delay
                event_type, details = "retry_scheduled", {"delay_s": delay.total_seconds()}
            job.lease_token = job.lease_expires_at = job.leased_by = None
            session.add(self._event(job, event_type, message=message, details=details))
        logger.warning(
            "job attempt failed",
            extra={"job_id": str(lease.job_id), "outcome": event_type, "reason": message},
        )
        return event_type

    async def defer(self, lease: Lease, error: QuotaDeferred) -> str:
        """Waits for provider quota; the attempt is given back (ADR-028)."""
        message = _error_text(error)
        async with self._sessions.begin() as session:
            job = await self._fenced(session, lease, {"last_error": message})
            job.status = "retry_scheduled"
            job.next_attempt_at = error.until
            job.attempts = max(0, job.attempts - 1)
            job.lease_token = job.lease_expires_at = job.leased_by = None
            details = {"until": error.until.isoformat()}
            session.add(self._event(job, "quota_deferred", message=message, details=details))
        logger.info(
            "job deferred for quota",
            extra={"job_id": str(lease.job_id), "until": error.until.isoformat()},
        )
        return "retry_scheduled"

    async def handle_error(self, lease: Lease, error: BaseException) -> str:
        """Ends the attempt according to ADR-007 and returns the job's new status."""
        if isinstance(error, QuotaDeferred):
            return await self.defer(lease, error)
        if isinstance(error, PermanentError):
            await self.fail(lease, error)
            return "failed"
        if isinstance(error, Cancelled):
            await self.cancel(lease)
            return "cancelled"
        return await self.schedule_retry(lease, error)

    # Admin actions on jobs nobody leases, or that only flag a running job. Each locks the row,
    # so a worker's SKIP LOCKED claim cannot interleave with them.

    async def _locked(self, session: AsyncSession, job_id: uuid.UUID) -> ProcessingJob:
        job = await session.scalar(
            select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
        )
        if job is None:
            raise JobNotFound(str(job_id))
        return job

    async def attach_source(self, video_id: uuid.UUID, media_asset_id: uuid.UUID) -> int:
        """Queues the video's jobs that were waiting for an authorized source."""
        now = self._clock()
        async with self._sessions.begin() as session:
            jobs = list(
                await session.scalars(
                    select(ProcessingJob)
                    .where(
                        ProcessingJob.video_id == video_id,
                        ProcessingJob.status == "awaiting_source",
                    )
                    .with_for_update()
                )
            )
            for job in jobs:
                job.media_asset_id = media_asset_id
                job.status = job.stage = "queued"
                job.next_attempt_at = now
                details = {"media_asset_id": str(media_asset_id)}
                session.add(self._event(job, "source_attached", details=details))
        return len(jobs)

    async def retry(self, job_id: uuid.UUID) -> ProcessingJob:
        async with self._sessions.begin() as session:
            job = await self._locked(session, job_id)
            if job.status != "failed":
                raise InvalidJobTransition(f"only failed jobs can be retried; job is {job.status}")
            job.status = "queued" if job.media_asset_id else "awaiting_source"
            job.attempts = 0
            job.next_attempt_at = self._clock()
            job.last_error = None
            job.finished_at = None
            job.cancel_requested = False
            session.add(self._event(job, "retried"))
            try:
                await session.flush()
            except IntegrityError:
                raise ActiveJobExists("the video already has an active job") from None
            await session.refresh(job)
        return job

    async def request_cancel(self, job_id: uuid.UUID) -> ProcessingJob:
        """Cancels a waiting job now; a running one stops at its next cancellation checkpoint."""
        async with self._sessions.begin() as session:
            job = await self._locked(session, job_id)
            if job.status not in ACTIVE_STATUSES:
                raise InvalidJobTransition(f"job is already {job.status}")
            job.cancel_requested = True
            if job.status == "running":
                session.add(self._event(job, "cancel_requested"))
            else:
                job.status = "cancelled"
                job.finished_at = self._clock()
                session.add(self._event(job, "cancelled"))
            await session.flush()
            await session.refresh(job)
        return job
