import asyncio
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select

from clipforge.api.deps import DbSession, SettingsDep
from clipforge.api.v1.schemas.jobs import CostOut, cost_lines, decimal_text
from clipforge.auth.deps import require_admin
from clipforge.db.models import Clip, ProcessingJob, ProviderUsage, YoutubeSource

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_admin)])

WEEK = timedelta(days=7)
MONTH = timedelta(days=30)
COMPLETED_STATUSES = ("completed", "completed_no_clips")
COST_PRECISION = Decimal("1e-8")


class DayCost(CostOut):
    day: date


class ModelCost(CostOut):
    provider: str
    model: str


class StageCost(CostOut):
    stage: str | None


class CostWindow(BaseModel):
    totals: list[CostOut]
    by_day: list[DayCost]
    by_model: list[ModelCost]
    by_stage: list[StageCost]


class DashboardCosts(BaseModel):
    """Estimated AI cost (ADR-024) from `provider_usage`; days are UTC."""

    last_7_days: CostWindow
    last_30_days: CostWindow
    average_per_completed_job: list[CostOut]
    completed_jobs: int


class DashboardOut(BaseModel):
    jobs_by_status: dict[str, int]
    clips_awaiting_review: int
    last_discovery_run_at: datetime | None
    disk_free_bytes: int | None
    costs: DashboardCosts


UsageKey = tuple[date, str, str, str | None, str]


def _window(rows: dict[UsageKey, Decimal]) -> CostWindow:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    by_day: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    by_model: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    by_stage: dict[tuple[str | None, str], Decimal] = defaultdict(Decimal)
    for (day, provider, model, stage, currency), cost in rows.items():
        totals[currency] += cost
        by_day[day, currency] += cost
        by_model[provider, model, currency] += cost
        by_stage[stage, currency] += cost
    return CostWindow(
        totals=cost_lines(dict(sorted(totals.items()))),
        by_day=[
            DayCost(day=day, currency=currency, estimated=decimal_text(cost))
            for (day, currency), cost in sorted(by_day.items())
        ],
        by_model=[
            ModelCost(provider=provider, model=model, currency=currency, estimated=decimal_text(c))
            for (provider, model, currency), c in sorted(by_model.items())
        ],
        by_stage=[
            StageCost(stage=stage, currency=currency, estimated=decimal_text(cost))
            for (stage, currency), cost in sorted(by_stage.items(), key=lambda i: str(i[0]))
        ],
    )


def _free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


@router.get("")
async def get_dashboard(session: DbSession, settings: SettingsDep) -> DashboardOut:
    """A fixed handful of statements regardless of data size."""
    now = func.now()
    completed_recently = (ProcessingJob.status.in_(COMPLETED_STATUSES)) & (
        ProcessingJob.finished_at >= now - MONTH
    )
    summary = (
        await session.execute(
            select(
                select(func.count())
                .where(Clip.review_status == "pending_review")
                .scalar_subquery(),
                select(func.max(YoutubeSource.last_run_at)).scalar_subquery(),
                select(func.count()).where(completed_recently).scalar_subquery(),
            )
        )
    ).one()
    statuses = await session.execute(
        select(ProcessingJob.status, func.count())
        .group_by(ProcessingJob.status)
        .order_by(ProcessingJob.status)
    )
    day = cast(func.timezone("UTC", ProviderUsage.created_at), Date)
    recent = (ProviderUsage.created_at >= now - WEEK).label("recent")
    usage = await session.execute(
        select(
            day,
            ProviderUsage.provider,
            ProviderUsage.model,
            ProviderUsage.stage,
            ProviderUsage.currency,
            recent,
            func.sum(ProviderUsage.estimated_cost),
        )
        .where(ProviderUsage.created_at >= now - MONTH)
        .group_by(
            day,
            ProviderUsage.provider,
            ProviderUsage.model,
            ProviderUsage.stage,
            ProviderUsage.currency,
            recent,
        )
    )
    completed_costs = await session.execute(
        select(ProviderUsage.currency, func.sum(ProviderUsage.estimated_cost))
        .join(ProcessingJob, ProcessingJob.id == ProviderUsage.job_id)
        .where(completed_recently)
        .group_by(ProviderUsage.currency)
        .order_by(ProviderUsage.currency)
    )

    month: dict[UsageKey, Decimal] = defaultdict(Decimal)
    week: dict[UsageKey, Decimal] = defaultdict(Decimal)
    for usage_day, provider, model, stage, currency, is_recent, cost in usage:
        key = (usage_day, provider, model, stage, currency)
        month[key] += cost
        if is_recent:
            week[key] += cost
    awaiting_review, last_discovery, completed_jobs = summary
    average = {
        currency: (total / completed_jobs).quantize(COST_PRECISION)
        for currency, total in completed_costs
        if completed_jobs
    }
    return DashboardOut(
        jobs_by_status={status: count for status, count in statuses},
        clips_awaiting_review=awaiting_review,
        last_discovery_run_at=last_discovery,
        disk_free_bytes=await asyncio.to_thread(_free_bytes, settings.workspace_dir),
        costs=DashboardCosts(
            last_7_days=_window(week),
            last_30_days=_window(month),
            average_per_completed_job=cost_lines(average),
            completed_jobs=completed_jobs,
        ),
    )
