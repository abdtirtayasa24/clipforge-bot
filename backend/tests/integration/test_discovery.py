from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from clipforge.db.models import AppSetting, Video, VideoSource, YoutubeSource
from clipforge.discovery.service import DiscoveryService
from clipforge.discovery.youtube_client import YouTubeClient
from tests.jobs import Sessions
from tests.youtube import (
    NOW,
    FakeYouTube,
    add_channel,
    add_source,
    add_trending,
    fixture_video_ids,
    source_links,
    stored_videos,
    update_discovery,
    vid,
)


def service(sessions: Sessions) -> DiscoveryService:
    client = YouTubeClient(httpx.AsyncClient(), "test-key")
    return DiscoveryService(sessions, client, clock=lambda: NOW)


@pytest.mark.usefixtures("clean_discovery")
async def test_trending_run_uses_dynamic_top_categories(
    sessions: Sessions, youtube: FakeYouTube
) -> None:
    await update_discovery(sessions, excluded_category_ids=["10"])
    source_id = await add_trending(sessions)

    report = await service(sessions).run()

    assert report.ok
    assert report.top_categories == ["24", "20", "17", "22", "25"]
    assert sorted(youtube.category_charts_requested()) == ["17", "20", "22", "24", "25"]
    assert report.quota_units == len(youtube.requests) == 10
    assert report.quota_units <= 12

    videos = await stored_videos(sessions)
    expected = {"m02", "m04", "m13", "m16", "m20", "c24a", "c24c", "c17a", "c22a", "c25a"}
    assert set(videos) == {vid(name) for name in expected}
    assert report.new_videos == len(expected)
    assert all(video.eligibility == "eligible" for video in videos.values())
    assert all(video.rights_status == "unverified" for video in videos.values())

    m02 = videos[vid("m02")]
    assert (m02.title, m02.category_id, m02.duration_seconds) == ("Video m02", "24", 2700)
    assert (m02.view_count, m02.channel_id) == (500000, "UCtrending00000000000001")
    assert m02.published_at == datetime(2026, 10, 19, 4, tzinfo=UTC)
    assert m02.thumbnail_url == f"https://i.ytimg.com/vi/{vid('m02')}/hqdefault.jpg"
    assert await source_links(sessions, vid("m02")) == [(source_id, "24")]
    assert await source_links(sessions, vid("m20")) == [(source_id, None)]


@pytest.mark.usefixtures("clean_discovery")
async def test_top_categories_follow_the_chart_without_exclusions(
    sessions: Sessions, youtube: FakeYouTube
) -> None:
    await add_trending(sessions)

    report = await service(sessions).run()

    assert report.top_categories == ["24", "10", "20", "17", "22"]
    videos = await stored_videos(sessions)
    assert {vid("m10"), vid("c10a")} <= set(videos)
    assert vid("c25a") not in videos


@pytest.mark.usefixtures("clean_discovery")
async def test_videos_outside_duration_range_are_skipped(
    sessions: Sessions, youtube: FakeYouTube
) -> None:
    await add_trending(sessions)

    report = await service(sessions).run()

    videos = await stored_videos(sessions)
    for name in ("m01", "m03", "m06", "m14", "m18", "c24b"):
        assert vid(name) not in videos
    assert {vid("m13"), vid("m16")} <= set(videos)
    assert report.skipped_ineligible > 0


@pytest.mark.usefixtures("clean_discovery")
async def test_second_run_creates_no_duplicates(sessions: Sessions, youtube: FakeYouTube) -> None:
    await add_trending(sessions)

    first = await service(sessions).run()
    videos_after_first = await stored_videos(sessions)
    second = await service(sessions).run()

    assert first.new_videos > 0
    assert second.new_videos == 0
    assert second.updated_videos == first.new_videos
    videos = await stored_videos(sessions)
    assert {key: video.id for key, video in videos.items()} == {
        key: video.id for key, video in videos_after_first.items()
    }
    async with sessions() as session:
        links = await session.scalar(
            select(func.count())
            .select_from(VideoSource)
            .join(Video, Video.id == VideoSource.video_id)
            .where(Video.youtube_video_id.in_(fixture_video_ids()))
        )
    assert links == len(videos)


@pytest.mark.usefixtures("clean_discovery")
async def test_video_surfaced_by_second_source_only_gains_a_link(
    sessions: Sessions, youtube: FakeYouTube
) -> None:
    trending_id = await add_trending(sessions)
    channel_id = await add_channel(sessions)

    report = await service(sessions).run()

    assert report.ok
    videos = await stored_videos(sessions)
    assert vid("ch1") in videos
    assert vid("ch2") not in videos
    links = await source_links(sessions, vid("m04"))
    assert sorted(source for source, _ in links) == sorted([trending_id, channel_id])
    assert await source_links(sessions, vid("ch1")) == [(channel_id, None)]


@pytest.mark.usefixtures("clean_discovery")
async def test_new_eligible_videos_are_ranked_by_views_per_hour_and_capped(
    sessions: Sessions, youtube: FakeYouTube
) -> None:
    await update_discovery(sessions, max_new_eligible_per_run=2)
    await add_trending(sessions)

    report = await service(sessions).run()

    assert set(await stored_videos(sessions)) == {vid("m10"), vid("m02")}
    assert report.new_videos == 2
    assert report.dropped_over_cap > 0


@pytest.mark.usefixtures("clean_discovery")
async def test_failing_source_is_reported_without_stopping_the_run(
    sessions: Sessions, youtube: FakeYouTube
) -> None:
    await add_trending(sessions)
    await add_channel(sessions, channel_id="UCunknown00000000000000x")

    report = await service(sessions).run()

    assert not report.ok
    assert report.failed_sources == ["Monitored"]
    assert vid("m02") in await stored_videos(sessions)


@pytest.mark.usefixtures("clean_discovery")
async def test_disabled_sources_are_not_run(sessions: Sessions, youtube: FakeYouTube) -> None:
    await add_source(sessions, kind="trending", name="Off", region_code="ID", enabled=False)

    report = await service(sessions).run()

    assert youtube.requests == []
    assert report.sources_run == 0


@pytest.mark.usefixtures("clean_discovery")
async def test_run_records_category_names_and_source_run_time(
    sessions: Sessions, youtube: FakeYouTube
) -> None:
    source_id = await add_trending(sessions)

    await service(sessions).run()

    async with sessions() as session:
        cached = await session.scalar(
            select(AppSetting.value).where(AppSetting.key == "youtube_categories")
        )
        source = await session.get_one(YoutubeSource, source_id)
    assert cached["region_code"] == "ID"
    assert cached["categories"]["24"] == "Entertainment"
    assert source.last_run_at == NOW
