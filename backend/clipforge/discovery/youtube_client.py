"""YouTube Data API v3 client for metadata discovery only (ADR-019); it never fetches media."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/youtube/v3"
MAX_RESULTS = 50
MAX_ATTEMPTS = 3
RETRY_DELAYS_S = (1.0, 4.0)
REQUEST_TIMEOUT_S = 20.0
_VIDEO_PARTS = "snippet,contentDetails,statistics"
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
# Every list call used here costs one quota unit, including failed requests.
_UNITS_PER_REQUEST = 1


class YouTubeApiError(Exception):
    def __init__(self, message: str, *, status: int | None, reason: str | None, transient: bool):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.transient = transient


class ChartNotFound(YouTubeApiError):
    """The region has no chart for the requested category."""


class _ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Thumbnail(_ApiModel):
    url: str


class Snippet(_ApiModel):
    published_at: datetime
    channel_id: str
    title: str
    description: str = ""
    channel_title: str | None = None
    category_id: str | None = None
    default_audio_language: str | None = None
    default_language: str | None = None
    thumbnails: dict[str, Thumbnail] = Field(default_factory=dict)

    def best_thumbnail_url(self) -> str | None:
        for size in ("high", "medium", "default"):
            if size in self.thumbnails:
                return self.thumbnails[size].url
        return None


class ContentDetails(_ApiModel):
    duration: str | None = None


class Statistics(_ApiModel):
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None


class VideoItem(_ApiModel):
    id: str
    snippet: Snippet
    content_details: ContentDetails = Field(default_factory=ContentDetails)
    statistics: Statistics = Field(default_factory=Statistics)


class VideoPage(_ApiModel):
    items: list[VideoItem] = Field(default_factory=list)
    next_page_token: str | None = None


def _error_reason(response: httpx.Response) -> str | None:
    try:
        errors = response.json()["error"]["errors"]
        reason = errors[0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return reason if isinstance(reason, str) else None


class YouTubeClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        # Sent as a header so the key never appears in URLs or access logs.
        self._headers = {"x-goog-api-key": api_key}
        self._sleep = sleep
        self.quota_units = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, resource: str, params: dict[str, str | int]) -> dict[str, Any]:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.quota_units += _UNITS_PER_REQUEST
            try:
                response = await self._http.get(
                    f"{BASE_URL}/{resource}",
                    params=params,
                    headers=self._headers,
                    timeout=REQUEST_TIMEOUT_S,
                )
            except httpx.TransportError as exc:
                error = YouTubeApiError(
                    f"{resource}.list request failed: {type(exc).__name__}",
                    status=None,
                    reason=None,
                    transient=True,
                )
            else:
                if response.is_success:
                    body: dict[str, Any] = response.json()
                    return body
                error = self._error(resource, response)
            if not error.transient or attempt == MAX_ATTEMPTS:
                raise error
            logger.warning(
                "youtube request failed; retrying",
                extra={"resource": resource, "status": error.status, "attempt": attempt},
            )
            await self._sleep(RETRY_DELAYS_S[attempt - 1])
        raise AssertionError("unreachable")

    @staticmethod
    def _error(resource: str, response: httpx.Response) -> YouTubeApiError:
        reason = _error_reason(response)
        message = f"{resource}.list returned HTTP {response.status_code} ({reason or 'unknown'})"
        if response.status_code == 404 and resource == "videos":
            return ChartNotFound(message, status=404, reason=reason, transient=False)
        transient = response.status_code in _TRANSIENT_STATUSES or reason == "rateLimitExceeded"
        return YouTubeApiError(
            message, status=response.status_code, reason=reason, transient=transient
        )

    async def most_popular(
        self,
        region_code: str,
        *,
        category_id: str | None = None,
        max_results: int = MAX_RESULTS,
        page_token: str | None = None,
    ) -> VideoPage:
        params: dict[str, str | int] = {
            "part": _VIDEO_PARTS,
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": max_results,
        }
        if category_id:
            params["videoCategoryId"] = category_id
        if page_token:
            params["pageToken"] = page_token
        return VideoPage.model_validate(await self._get("videos", params))

    async def videos(self, video_ids: Sequence[str]) -> list[VideoItem]:
        items: list[VideoItem] = []
        for start in range(0, len(video_ids), MAX_RESULTS):
            batch = ",".join(video_ids[start : start + MAX_RESULTS])
            body = await self._get("videos", {"part": _VIDEO_PARTS, "id": batch})
            items.extend(VideoPage.model_validate(body).items)
        return items

    async def video_categories(self, region_code: str) -> dict[str, str]:
        body = await self._get("videoCategories", {"part": "snippet", "regionCode": region_code})
        return {item["id"]: item["snippet"]["title"] for item in body.get("items", [])}

    async def uploads_playlist_id(self, channel_id: str) -> str | None:
        body = await self._get("channels", {"part": "contentDetails", "id": channel_id})
        items = body.get("items", [])
        if not items:
            return None
        uploads = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        return uploads if isinstance(uploads, str) and uploads else None

    async def playlist_video_ids(self, playlist_id: str) -> list[str]:
        body = await self._get(
            "playlistItems",
            {"part": "contentDetails", "playlistId": playlist_id, "maxResults": MAX_RESULTS},
        )
        return [item["contentDetails"]["videoId"] for item in body.get("items", [])]
