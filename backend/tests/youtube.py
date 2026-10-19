import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import insert, select, update

from clipforge.db.models import AppSetting, Video, VideoSource, YoutubeSource
from tests.jobs import Sessions

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "youtube"
NOW = datetime(2026, 10, 20, tzinfo=UTC)
MONITORED_CHANNEL = "UCmonitored0000000000001"
CHART_PAGES = {None: 1, "CAUQAA": 2, "CAoQAA": 3, "CA8QAA": 4}


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def vid(name: str) -> str:
    return name.upper().ljust(11, "0")


def fixture_video_ids() -> set[str]:
    ids: set[str] = set()
    for path in FIXTURES.glob("*.json"):
        for item in fixture(path.name).get("items", []):
            if item.get("kind") == "youtube#video":
                ids.add(item["id"])
    return ids


class FakeYouTube:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def category_charts_requested(self) -> list[str]:
        return [
            request.url.params["videoCategoryId"]
            for request in self.requests
            if "videoCategoryId" in request.url.params
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        resource = request.url.path.rsplit("/", 1)[-1]
        params = request.url.params
        if resource == "videos" and params.get("chart") == "mostPopular":
            category = params.get("videoCategoryId")
            if category == "20":
                return httpx.Response(404, json=fixture("chart_ID_category_20_not_found.json"))
            if category:
                return httpx.Response(200, json=fixture(f"chart_ID_category_{category}.json"))
            page = CHART_PAGES[params.get("pageToken")]
            return httpx.Response(200, json=fixture(f"chart_ID_page{page}.json"))
        if resource == "videos":
            return httpx.Response(200, json=fixture("videos_monitored.json"))
        if resource == "videoCategories":
            return httpx.Response(200, json=fixture("video_categories_ID.json"))
        if resource == "channels":
            name = "monitored" if params["id"] == MONITORED_CHANNEL else "not_found"
            return httpx.Response(200, json=fixture(f"channels_{name}.json"))
        if resource == "playlistItems":
            return httpx.Response(200, json=fixture("playlist_items_monitored.json"))
        raise AssertionError(f"unexpected request {request.url}")


async def set_discovery(sessions: Sessions, value: dict[str, Any]) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(AppSetting).where(AppSetting.key == "discovery").values(value=value)
        )


async def update_discovery(sessions: Sessions, **changes: Any) -> None:
    async with sessions() as session:
        current = await session.scalar(
            select(AppSetting.value).where(AppSetting.key == "discovery")
        )
    await set_discovery(sessions, {**current, **changes})


async def add_source(sessions: Sessions, **values: Any) -> uuid.UUID:
    async with sessions.begin() as session:
        source_id = await session.scalar(
            insert(YoutubeSource).values(**values).returning(YoutubeSource.id)
        )
    assert source_id is not None
    return source_id


async def add_trending(sessions: Sessions) -> uuid.UUID:
    return await add_source(sessions, kind="trending", name="Trending ID", region_code="ID")


async def add_channel(sessions: Sessions, channel_id: str = MONITORED_CHANNEL) -> uuid.UUID:
    return await add_source(sessions, kind="channel", name="Monitored", channel_id=channel_id)


async def stored_videos(sessions: Sessions) -> dict[str, Video]:
    async with sessions() as session:
        videos = await session.scalars(
            select(Video).where(Video.youtube_video_id.in_(fixture_video_ids()))
        )
        return {video.youtube_video_id: video for video in videos}


async def source_links(
    sessions: Sessions, youtube_video_id: str
) -> list[tuple[uuid.UUID, str | None]]:
    async with sessions() as session:
        rows = await session.execute(
            select(VideoSource.source_id, VideoSource.category_id)
            .join(Video, Video.id == VideoSource.video_id)
            .where(Video.youtube_video_id == youtube_video_id)
            .order_by(VideoSource.first_seen_at)
        )
        return [(row.source_id, row.category_id) for row in rows]
