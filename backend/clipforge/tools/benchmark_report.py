"""Benchmark report (T42): python -m clipforge.tools.benchmark_report [--resources LOG]

Per-stage latency, upload duration and AI usage come from `job_events` and `provider_usage`;
memory, disk and CPU from the resource log sampled from systemd accounting while the jobs ran
(docs/benchmark-report.md). The report ends with the sustainable videos/day and a recommended
`daily_job_cap`; the admin sets it in the Mini App so the change is audited.
"""

import argparse
import asyncio
import itertools
import logging
import math
import statistics
import sys
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clipforge.config import Settings
from clipforge.db.models import AiProviderConfig, Clip, JobEvent, ProcessingJob, ProviderUsage
from clipforge.db.session import create_engine, create_session_factory
from clipforge.ingest.disk import DEFAULT_RESERVED_DISK_BYTES, reserved_disk_bytes
from clipforge.logging import configure_logging

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_NO_DATA, EXIT_CONFIG = 0, 1, 2
STAGES = (
    "validated",
    "audio_extracted",
    "transcribed",
    "analyzed",
    "candidates_selected",
    "rendering",
    "uploading",
    "affiliate_matching",
)
FINISHED = ("completed", "completed_no_clips", "failed")
ATTEMPT_STARTS = ("claimed", "reclaimed")
CANDIDATE_CAPS = (5, 10, 20)
# One worker processes jobs one at a time; leave a fifth of the day for retries and slack.
WORKER_UTILIZATION = 0.8
VCPUS = 2
MEMORY_LIMIT_BYTES = 1800 * 1024**2  # MemoryMax of clipforge-worker.service
SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class Marker:
    event_type: str
    stage: str | None
    at: datetime


@dataclass
class JobRun:
    id: uuid.UUID
    status: str
    started_at: datetime
    finished_at: datetime
    clips: int
    stage_seconds: dict[str, float]
    costs: dict[str, Decimal]

    @property
    def wall_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class Resources:
    samples: int
    peak_memory_bytes: int
    min_free_bytes: int
    peak_disk_used_bytes: int
    cpu_seconds: float | None
    wall_seconds: float


@dataclass
class Report:
    jobs: list[JobRun]
    requests_per_job: dict[str, float] = field(default_factory=dict)
    rpd_limits: dict[str, int] = field(default_factory=dict)
    resources: Resources | None = None
    reserve_bytes: int = DEFAULT_RESERVED_DISK_BYTES


def stage_seconds(markers: Sequence[Marker]) -> dict[str, float]:
    """Seconds per finished stage: from the previous marker of the same attempt to its event.

    A stage is recorded when it finishes, so the time since the attempt started or the previous
    stage finished is that stage's run time; waits between attempts are not counted.
    """
    durations: dict[str, float] = defaultdict(float)
    previous: datetime | None = None
    for marker in markers:
        if marker.event_type in ATTEMPT_STARTS:
            previous = marker.at
        elif marker.event_type == "stage_changed" and previous is not None:
            if marker.stage in STAGES:
                durations[marker.stage] += (marker.at - previous).total_seconds()
            previous = marker.at
    return dict(durations)


def parse_resources(lines: Iterable[str]) -> Resources | None:
    """Reads `HH:MM:SS mem=N peak=N free=N [cpu=N]` lines; cpu is CPUUsageNSec (cumulative)."""
    samples: list[dict[str, int]] = []
    times: list[datetime] = []
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        values = {}
        for part in parts[1:]:
            key, _, raw = part.partition("=")
            if raw.isdigit():
                values[key] = int(raw)
        if "free" not in values or not ({"mem", "peak"} & values.keys()):
            continue
        try:
            times.append(datetime.strptime(parts[0], "%H:%M:%S"))
        except ValueError:
            continue
        samples.append(values)
    if not samples:
        return None
    wall = sum(
        (later - earlier).total_seconds() % SECONDS_PER_DAY
        for earlier, later in itertools.pairwise(times)
    )
    frees = [sample["free"] for sample in samples]
    cpu = [sample["cpu"] for sample in samples if "cpu" in sample]
    return Resources(
        samples=len(samples),
        peak_memory_bytes=max(max(s.get("peak", 0), s.get("mem", 0)) for s in samples),
        min_free_bytes=min(frees),
        peak_disk_used_bytes=max(0, frees[0] - min(frees)),
        cpu_seconds=(cpu[-1] - cpu[0]) / 1e9 if len(cpu) >= 2 else None,
        wall_seconds=wall,
    )


def percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


def capacity_by_time(report: Report) -> int:
    p90 = percentile([job.wall_seconds for job in report.jobs], 0.9)
    return math.floor(SECONDS_PER_DAY * WORKER_UTILIZATION / max(p90, 1.0))


def capacity_by_quota(report: Report) -> int | None:
    """Videos/day before the tightest provider's requests-per-day limit is used up."""
    limits = [
        math.floor(report.rpd_limits[model] / per_job)
        for model, per_job in report.requests_per_job.items()
        if model in report.rpd_limits and per_job > 0
    ]
    return min(limits) if limits else None


def resource_blockers(report: Report) -> list[str]:
    resources = report.resources
    if resources is None:
        return []
    blockers = []
    if resources.peak_memory_bytes >= MEMORY_LIMIT_BYTES:
        blockers.append("worker memory reached MemoryMax")
    if resources.min_free_bytes < report.reserve_bytes:
        blockers.append("free disk fell below the reserve")
    return blockers


def recommended_cap(report: Report) -> int:
    limits = [capacity_by_time(report)]
    if (quota := capacity_by_quota(report)) is not None:
        limits.append(quota)
    sustainable = max(1, min(limits))
    if resource_blockers(report):
        return min(CANDIDATE_CAPS[0], sustainable)
    fitting = [cap for cap in CANDIDATE_CAPS if cap <= sustainable]
    return fitting[-1] if fitting else sustainable


def _gib(value: int) -> str:
    return f"{value / 1024**3:.2f} GiB"


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f} min"


def _costs(costs: dict[str, Decimal]) -> str:
    return ", ".join(f"{total:.4f} {currency}" for currency, total in sorted(costs.items())) or "0"


def render(report: Report) -> str:
    jobs = report.jobs
    walls = [job.wall_seconds for job in jobs]
    lines = [
        "## Measured jobs",
        "",
        "| Job | Status | Clips | Wall time | Upload | AI cost |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for job in jobs:
        upload = job.stage_seconds.get("uploading")
        lines.append(
            f"| `{str(job.id)[:8]}` | {job.status} | {job.clips} | {_minutes(job.wall_seconds)} "
            f"| {'—' if upload is None else f'{upload:.0f} s'} | {_costs(job.costs)} |"
        )
    lines += [
        "",
        "## Per-stage latency (seconds)",
        "",
        "| Stage | Mean | p90 | Max |",
        "|---|---:|---:|---:|",
    ]
    for stage in STAGES:
        values = [job.stage_seconds[stage] for job in jobs if stage in job.stage_seconds]
        if values:
            lines.append(
                f"| {stage} | {statistics.fmean(values):.1f} | {percentile(values, 0.9):.1f} "
                f"| {max(values):.1f} |"
            )
    total_cost: dict[str, Decimal] = defaultdict(Decimal)
    for job in jobs:
        for currency, amount in job.costs.items():
            total_cost[currency] += amount
    per_job = {currency: amount / len(jobs) for currency, amount in total_cost.items()}
    lines += [
        "",
        "## Totals",
        "",
        f"- Jobs: {len(jobs)} ({sum(job.status != 'failed' for job in jobs)} succeeded)",
        f"- Wall time per job: mean {_minutes(statistics.fmean(walls))}, "
        f"p90 {_minutes(percentile(walls, 0.9))}",
        f"- Clips per source: mean {statistics.fmean(job.clips for job in jobs):.1f}, "
        f"max {max(job.clips for job in jobs)}",
        f"- Estimated AI cost per job: {_costs(per_job)}",
    ]
    for model, per_job_requests in sorted(report.requests_per_job.items()):
        limit = report.rpd_limits.get(model)
        quota = f" (RPD limit {limit})" if limit else ""
        lines.append(f"- AI requests per job, {model}: {per_job_requests:.1f}{quota}")
    resources = report.resources
    lines += ["", "## Resources", ""]
    if resources is None:
        lines.append("- No resource log given (`--resources`).")
    else:
        lines += [
            f"- Samples: {resources.samples} over {_minutes(resources.wall_seconds)}",
            f"- Peak worker memory: {_gib(resources.peak_memory_bytes)} "
            f"(limit {_gib(MEMORY_LIMIT_BYTES)})",
            f"- Peak disk used: {_gib(resources.peak_disk_used_bytes)}; "
            f"lowest free: {_gib(resources.min_free_bytes)} (reserve {_gib(report.reserve_bytes)})",
        ]
        if resources.cpu_seconds is not None and resources.wall_seconds:
            share = resources.cpu_seconds / resources.wall_seconds / VCPUS
            lines.append(
                f"- Worker CPU: {resources.cpu_seconds:.0f} s, {share:.0%} of {VCPUS} vCPUs"
            )
    time_cap = capacity_by_time(report)
    quota_cap = capacity_by_quota(report)
    cap = recommended_cap(report)
    lines += [
        "",
        "## Capacity",
        "",
        f"- By worker time (one job at a time, {WORKER_UTILIZATION:.0%} busy, p90 wall time): "
        f"{time_cap} videos/day",
        f"- By AI quota (requests per day): "
        f"{'no limit configured' if quota_cap is None else f'{quota_cap} videos/day'}",
    ]
    lines += [f"- Blocker: {blocker}" for blocker in resource_blockers(report)]
    for target in CANDIDATE_CAPS:
        fits = target <= min(time_cap, quota_cap or target) and not resource_blockers(report)
        cost = _costs({currency: amount * target for currency, amount in per_job.items()})
        lines.append(
            f"- {target}/day: {'sustainable' if fits else 'not sustainable'}, AI ~{cost}/day"
        )
    lines += [
        "",
        f"**Recommended `daily_job_cap`: {cap}** "
        "(Mini App → Settings → Clip generation → Daily job cap).",
    ]
    return "\n".join(lines) + "\n"


async def load_report(session: AsyncSession, *, since: datetime | None, limit: int) -> Report:
    query = (
        select(ProcessingJob)
        .where(
            ProcessingJob.status.in_(FINISHED),
            ProcessingJob.started_at.is_not(None),
            ProcessingJob.finished_at.is_not(None),
        )
        .order_by(ProcessingJob.started_at.desc())
        .limit(limit)
    )
    if since is not None:
        query = query.where(ProcessingJob.started_at >= since)
    rows = list(await session.scalars(query))
    ids = [job.id for job in rows]
    markers: dict[uuid.UUID, list[Marker]] = defaultdict(list)
    for job_id, event_type, stage, at in await session.execute(
        select(JobEvent.job_id, JobEvent.event_type, JobEvent.stage, JobEvent.created_at)
        .where(JobEvent.job_id.in_(ids))
        .order_by(JobEvent.id)
    ):
        markers[job_id].append(Marker(event_type, stage, at))
    clips: dict[uuid.UUID, int] = {
        job_id: count
        for job_id, count in await session.execute(
            select(Clip.job_id, func.count()).where(Clip.job_id.in_(ids)).group_by(Clip.job_id)
        )
    }
    costs: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(dict)
    requests: dict[str, int] = defaultdict(int)
    for usage_job_id, provider, model, currency, total, calls in await session.execute(
        select(
            ProviderUsage.job_id,
            ProviderUsage.provider,
            ProviderUsage.model,
            ProviderUsage.currency,
            func.sum(ProviderUsage.estimated_cost),
            func.count(),
        )
        .where(ProviderUsage.job_id.in_(ids))
        .group_by(
            ProviderUsage.job_id,
            ProviderUsage.provider,
            ProviderUsage.model,
            ProviderUsage.currency,
        )
    ):
        if usage_job_id is None:
            continue
        job_costs = costs[usage_job_id]
        job_costs[currency] = job_costs.get(currency, Decimal(0)) + total
        requests[f"{provider}:{model}"] += calls
    rpd_limits = {
        f"{provider}:{model}": limit
        for provider, model, limit in await session.execute(
            select(AiProviderConfig.provider, AiProviderConfig.model, AiProviderConfig.rpd_limit)
        )
        if limit is not None
    }
    jobs = [
        JobRun(
            id=job.id,
            status=job.status,
            started_at=job.started_at,  # type: ignore[arg-type]
            finished_at=job.finished_at,  # type: ignore[arg-type]
            clips=clips.get(job.id, 0),
            stage_seconds=stage_seconds(markers[job.id]),
            costs=costs[job.id],
        )
        for job in reversed(rows)
    ]
    return Report(
        jobs=jobs,
        requests_per_job={model: calls / len(jobs) for model, calls in requests.items()}
        if jobs
        else {},
        rpd_limits=rpd_limits,
        reserve_bytes=await reserved_disk_bytes(session),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m clipforge.tools.benchmark_report")
    parser.add_argument(
        "--since", type=datetime.fromisoformat, help="only jobs started at or after this time"
    )
    parser.add_argument("--limit", type=int, default=50, help="most recent finished jobs to use")
    parser.add_argument("--resources", type=Path, help="resource log sampled during the run")
    parser.add_argument("--output", type=Path, help="write the report here instead of stdout")
    return parser


async def _run(args: argparse.Namespace, database_url: str) -> int:
    engine = create_engine(database_url, pool_size=1)
    try:
        async with create_session_factory(engine)() as session:
            report = await load_report(session, since=args.since, limit=args.limit)
    finally:
        await engine.dispose()
    if not report.jobs:
        logger.error("no finished jobs to report on")
        return EXIT_NO_DATA
    if args.resources is not None:
        report.resources = parse_resources(args.resources.read_text(encoding="utf-8").splitlines())
    text = render(report)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    if settings.database_url is None:
        logger.error("CLIPFORGE_DATABASE_URL is not set")
        return EXIT_CONFIG
    # psycopg's async mode cannot run on Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(
        _run(args, settings.database_url.get_secret_value()), loop_factory=loop_factory
    )


if __name__ == "__main__":
    sys.exit(main())
