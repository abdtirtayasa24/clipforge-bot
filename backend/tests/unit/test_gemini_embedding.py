import json
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
import respx

from clipforge.ai.adapters.gemini_client import BASE_URL, GeminiClient
from clipforge.ai.adapters.gemini_embedding import MAX_BATCH, GeminiEmbeddingProvider
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.usage import ProviderCall
from clipforge.settings_schema import ProviderSnapshot
from tests.clock import FakeClock

URL = f"{BASE_URL}/v1beta/models/gemini-embedding-2:batchEmbedContents"


class Recorder:
    def __init__(self) -> None:
        self.calls: list[ProviderCall] = []

    async def __call__(self, call: ProviderCall) -> None:
        self.calls.append(call)


async def no_sessions() -> None:
    raise AssertionError("no database access expected")


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
def http() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def embedder() -> GeminiEmbeddingProvider:
    snapshot = ProviderSnapshot(
        provider="gemini",
        model="gemini-embedding-2",
        capability="embedding",
        secret_ref="GEMINI_API_KEY",
    )
    quota = QuotaGuard(no_sessions, clock=FakeClock(datetime(2026, 10, 21, tzinfo=UTC)))  # type: ignore[arg-type]
    return GeminiEmbeddingProvider(
        GeminiClient(httpx.AsyncClient(), "key", snapshot, quota=quota, sleep=no_sleep)
    )


def reply(request: httpx.Request) -> httpx.Response:
    texts = [r["content"]["parts"][0]["text"] for r in json.loads(request.content)["requests"]]
    return httpx.Response(
        200, json={"embeddings": [{"values": [float(len(text)), 1.0]} for text in texts]}
    )


async def test_texts_are_embedded_in_order_with_batches_and_usage(
    http: respx.MockRouter,
) -> None:
    route = http.post(URL).mock(side_effect=reply)
    texts = [f"text {'x' * i}" for i in range(MAX_BATCH + 2)]
    record = Recorder()

    vectors = await embedder().embed(texts, record=record)

    assert vectors == [[float(len(text)), 1.0] for text in texts]
    assert route.call_count == 2
    first = json.loads(route.calls[0].request.content)["requests"][0]
    assert first["model"] == "models/gemini-embedding-2"
    assert route.calls[0].request.headers["x-goog-api-key"] == "key"
    assert [call.success for call in record.calls] == [True, True]


async def test_empty_input_makes_no_request(http: respx.MockRouter) -> None:
    route = http.post(URL)

    assert await embedder().embed([], record=Recorder()) == []
    assert route.call_count == 0


async def test_mismatched_response_is_an_error(http: respx.MockRouter) -> None:
    http.post(URL).mock(return_value=httpx.Response(200, json={"embeddings": []}))

    with pytest.raises(ValueError, match="embeddings"):
        await embedder().embed(["a"], record=Recorder())
