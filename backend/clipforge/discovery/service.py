"""Daily YouTube discovery: trending in the configured region plus monitored channels (ADR-019)."""

import logging
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from clipforge.db.models import AppSetting, Video, VideoSource, YoutubeSource
from clipforge.discovery.duration import parse_iso8601_duration
from clipforge.discovery.regions import ISO_3166_ALPHA2
from clipforge.discovery.youtube_client import (
    ChartNotFound,
    VideoItem,
    YouTubeApiError,
    YouTubeClient,
)
from clipforge.ingest.validation import MAX_DURATION_S, MIN_DURATION_S
from clipforge.queue.repository import Clock, utc_now

logger = logging.getLogger(__name__)

CATEGORY_CACHE_KEY = "youtube_categories"
# Session-level advisory lock shared by the timer, the CLI and the API so runs never overlap.
DISCOVERY_LOCK_KEY = 0x636C6970_64697363


class DiscoveryAlreadyRunning(Exception):
    pass


class DiscoverySettings(BaseModel):
    region_code: str = Field(default="ID", pattern=r"^[A-Z]{2}$")
    top_categories: int = Field(default=5, ge=1, le=20)
    chart_pages: int = Field(default=4, ge=1, le=4)
    per_category_results: int = Field(default=50, ge=1, le=50)
    max_new_eligible_per_run: int = Field(default=20, ge=1, le=50)
    excluded_category_ids: list[str] = Field(default_factory=list)

    @field_validator("region_code")
    @classmethod
    def _iso_region(cls, value: str) -> str:
        if value not in ISO_3166_ALPHA2:
            raise ValueError("region_code must be an ISO 3166-1 alpha-2 code")
        return value


@dataclass
class DiscoveryReport:
    sources_run: int = 0
    failed_sources: list[str] = field(default_factory=list)
    top_categories: list[str] = field(default_factory=list)
    quota_units: int = 0
    new_videos: int = 0
    updated_videos: int = 0
    skipped_ineligible: int = 0
    dropped_over_cap: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed_sources


@dataclass
class _Candidate:
    item: VideoItem
    links: dict[uuid.UUID, str | None] = field(default_factory=dict)


def _duration_s(item: VideoItem) -> int | None:
    if item.content_details.duration is None:
        return None
    try:
        return parse_iso8601_duration(item.content_details.duration)
    except ValueError:
        return None


def _eligible(item: VideoItem) -> bool:
    duration = _duration_s(item)
    return duration is not None and MIN_DURATION_S <= duration <= MAX_DURATION_S


def _views_per_hour(item: VideoItem, now: datetime) -> float:
    hours = max((now - item.snippet.published_at).total_seconds() / 3600, 1.0)
    return (item.statistics.view_count or 0) / hours


def _video_values(item: VideoItem) -> dict[str, Any]:
    snippet = item.snippet
    return {
        "title": snippet.title,
        "description": snippet.description,
        "channel_id": snippet.channel_id,
        "channel_title": snippet.channel_title,
        "category_id": snippet.category_id,
        "duration_seconds": _duration_s(item),
        "published_at": snippet.published_at,
        "view_count": item.statistics.view_count,
        "like_count": item.statistics.like_count,
        "comment_count": item.statistics.comment_count,
        "default_language": snippet.default_audio_language or snippet.default_language,
        "thumbnail_url": snippet.best_thumbnail_url(),
    }


def top_categories(items: list[VideoItem], count: int, excluded: set[str]) -> list[str]:
    counts = Counter(
        item.snippet.category_id
        for item in items
        if item.snippet.category_id and item.snippet.category_id not in excluded
    )
    ranked = sorted(counts.items(), key=lambda entry: (-entry[1], int(entry[0])))
    return [category for category, _ in ranked[:count]]


class DiscoveryService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: YouTubeClient,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._client = client
        self._clock = clock

    async def run(self) -> DiscoveryReport:
        now = self._clock()
        report = DiscoveryReport()
        async with self._sessions() as session:
            raw_settings = await session.scalar(
                select(AppSetting.value).where(AppSetting.key == "discovery")
            )
            sources = list(
                await session.scalars(
                    select(YoutubeSource)
                    .where(YoutubeSource.enabled)
                    .order_by(YoutubeSource.created_at)
                )
            )
        settings = DiscoverySettings.model_validate(raw_settings or {})
        candidates: dict[str, _Candidate] = {}
        succeeded: list[uuid.UUID] = []

        for source in sources:
            report.sources_run += 1
            try:
                # The schema guarantees channel sources, and only those, have a channel_id.
                if source.channel_id is None:
                    found = await self._trending(source, settings, report)
                else:
                    found = await self._channel(source.channel_id)
            except YouTubeApiError as exc:
                report.failed_sources.append(source.name)
                logger.error(
                    "discovery source failed",
                    extra={"source_id": str(source.id), "reason": str(exc)},
                )
                continue
            succeeded.append(source.id)
            for item, category_id in found:
                candidate = candidates.setdefault(item.id, _Candidate(item))
                if candidate.links.get(source.id) is None:
                    candidate.links[source.id] = category_id

        await self._persist(candidates, settings, now, report)
        async with self._sessions.begin() as session:
            await session.execute(
                update(YoutubeSource).where(YoutubeSource.id.in_(succeeded)).values(last_run_at=now)
            )
        report.quota_units = self._client.quota_units
        logger.info("discovery finished", extra={"report": vars(report)})
        return report

    async def _trending(
        self, source: YoutubeSource, settings: DiscoverySettings, report: DiscoveryReport
    ) -> list[tuple[VideoItem, str | None]]:
        region = source.region_code or settings.region_code
        excluded = set(settings.excluded_category_ids)
        chart: list[VideoItem] = []
        page_token: str | None = None
        for _ in range(settings.chart_pages):
            page = await self._client.most_popular(region, page_token=page_token)
            chart.extend(page.items)
            page_token = page.next_page_token
            if page_token is None:
                break
        await self._cache_categories(region)

        categories = top_categories(chart, settings.top_categories, excluded)
        report.top_categories = categories
        found: list[tuple[VideoItem, str | None]] = [
            (item, None) for item in chart if item.snippet.category_id not in excluded
        ]
        for category_id in categories:
            try:
                page = await self._client.most_popular(
                    region, category_id=category_id, max_results=settings.per_category_results
                )
            except ChartNotFound:
                logger.info("category has no chart", extra={"category_id": category_id})
                continue
            found.extend((item, category_id) for item in page.items)
        return found

    async def _channel(self, channel_id: str) -> list[tuple[VideoItem, str | None]]:
        playlist_id = await self._client.uploads_playlist_id(channel_id)
        if playlist_id is None:
            raise YouTubeApiError(
                "channel not found", status=None, reason="channelNotFound", transient=False
            )
        video_ids = await self._client.playlist_video_ids(playlist_id)
        return [(item, None) for item in await self._client.videos(video_ids)]

    async def _cache_categories(self, region: str) -> None:
        names = await self._client.video_categories(region)
        value = {"region_code": region, "categories": names}
        async with self._sessions.begin() as session:
            await session.execute(
                insert(AppSetting)
                .values(key=CATEGORY_CACHE_KEY, value=value)
                .on_conflict_do_update(index_elements=["key"], set_={"value": value})
            )

    async def _persist(
        self,
        candidates: dict[str, _Candidate],
        settings: DiscoverySettings,
        now: datetime,
        report: DiscoveryReport,
    ) -> None:
        async with self._sessions.begin() as session:
            rows = await session.execute(
                select(Video.youtube_video_id, Video.id).where(
                    Video.youtube_video_id.in_(candidates)
                )
            )
            existing = {youtube_id: video_id for youtube_id, video_id in rows}
            new_eligible = []
            for youtube_id, candidate in candidates.items():
                if youtube_id in existing:
                    await session.execute(
                        update(Video)
                        .where(Video.id == existing[youtube_id])
                        .values(_video_values(candidate.item))
                    )
                    await self._link(session, existing[youtube_id], candidate, now)
                    report.updated_videos += 1
                elif _eligible(candidate.item):
                    new_eligible.append(candidate)
                else:
                    report.skipped_ineligible += 1

            new_eligible.sort(
                key=lambda candidate: _views_per_hour(candidate.item, now), reverse=True
            )
            kept = new_eligible[: settings.max_new_eligible_per_run]
            report.dropped_over_cap = len(new_eligible) - len(kept)
            for candidate in kept:
                video_id = await session.scalar(
                    insert(Video)
                    .values(
                        youtube_video_id=candidate.item.id,
                        eligibility="eligible",
                        discovered_at=now,
                        **_video_values(candidate.item),
                    )
                    .on_conflict_do_nothing(index_elements=["youtube_video_id"])
                    .returning(Video.id)
                )
                if video_id is not None:
                    await self._link(session, video_id, candidate, now)
                    report.new_videos += 1

    @staticmethod
    async def _link(
        session: AsyncSession, video_id: uuid.UUID, candidate: _Candidate, now: datetime
    ) -> None:
        for source_id, category_id in candidate.links.items():
            statement = insert(VideoSource).values(
                video_id=video_id,
                source_id=source_id,
                category_id=category_id,
                first_seen_at=now,
                last_seen_at=now,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["video_id", "source_id"],
                    set_={"last_seen_at": now},
                )
            )


async def run_exclusive(
    engine: AsyncEngine,
    api_key: str,
    *,
    on_lock: Callable[[bool], None] = lambda _acquired: None,
) -> DiscoveryReport:
    """Runs discovery while holding the discovery lock; `on_lock` reports whether it was free."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.connect() as lock_connection:
        acquired = bool(
            await lock_connection.scalar(select(func.pg_try_advisory_lock(DISCOVERY_LOCK_KEY)))
        )
        await lock_connection.commit()
        on_lock(acquired)
        if not acquired:
            raise DiscoveryAlreadyRunning("discovery is already running")
        try:
            async with httpx.AsyncClient() as http:
                return await DiscoveryService(sessions, YouTubeClient(http, api_key)).run()
        finally:
            # Closing the session releases the lock; a pooled connection would keep holding it.
            await lock_connection.invalidate()
