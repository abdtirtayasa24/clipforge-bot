import httpx
import pytest
import respx

from clipforge.discovery.youtube_client import (
    BASE_URL,
    ChartNotFound,
    YouTubeApiError,
    YouTubeClient,
)

VIDEOS_URL = f"{BASE_URL}/videos"


def error_body(status: int, reason: str) -> dict[str, object]:
    return {"error": {"code": status, "errors": [{"reason": reason}], "message": reason}}


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
def client() -> YouTubeClient:
    return YouTubeClient(httpx.AsyncClient(), "test-key", sleep=no_sleep)


@respx.mock
async def test_api_key_is_sent_as_header_not_in_url(client: YouTubeClient) -> None:
    route = respx.get(VIDEOS_URL).respond(json={"items": []})

    await client.most_popular("ID")

    request = route.calls.last.request
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in str(request.url)
    assert request.url.params["chart"] == "mostPopular"
    assert request.url.params["regionCode"] == "ID"
    assert client.quota_units == 1


@respx.mock
async def test_server_errors_and_network_failures_are_retried(client: YouTubeClient) -> None:
    respx.get(VIDEOS_URL).mock(
        side_effect=[
            httpx.Response(503, json=error_body(503, "backendError")),
            httpx.ConnectError("reset"),
            httpx.Response(200, json={"items": []}),
        ]
    )

    page = await client.most_popular("ID")

    assert page.items == []
    assert client.quota_units == 3


@respx.mock
async def test_retries_are_bounded(client: YouTubeClient) -> None:
    respx.get(VIDEOS_URL).respond(500, json=error_body(500, "backendError"))

    with pytest.raises(YouTubeApiError) as error:
        await client.most_popular("ID")

    assert error.value.transient
    assert client.quota_units == 3


@respx.mock
async def test_quota_exceeded_is_not_retried(client: YouTubeClient) -> None:
    respx.get(VIDEOS_URL).respond(403, json=error_body(403, "quotaExceeded"))

    with pytest.raises(YouTubeApiError) as error:
        await client.most_popular("ID")

    assert error.value.reason == "quotaExceeded"
    assert not error.value.transient
    assert client.quota_units == 1


@respx.mock
async def test_missing_category_chart_raises_chart_not_found(client: YouTubeClient) -> None:
    respx.get(VIDEOS_URL).respond(404, json=error_body(404, "videoChartNotFound"))

    with pytest.raises(ChartNotFound):
        await client.most_popular("ID", category_id="20")


@respx.mock
async def test_error_message_does_not_leak_the_api_key(client: YouTubeClient) -> None:
    respx.get(VIDEOS_URL).respond(400, json=error_body(400, "keyInvalid"))

    with pytest.raises(YouTubeApiError) as error:
        await client.most_popular("ID")

    assert "test-key" not in str(error.value)
