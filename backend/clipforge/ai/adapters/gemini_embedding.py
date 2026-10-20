"""Gemini text embeddings for affiliate matching (ADR-020); batched, quota-guarded."""

from collections.abc import Callable, Sequence

import httpx

from clipforge.ai.adapters.gemini_client import GeminiClient
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.usage import UsageRecorder
from clipforge.settings_schema import ProviderSnapshot

MAX_BATCH = 100


class GeminiEmbeddingProvider:
    def __init__(self, client: GeminiClient) -> None:
        self._client = client

    async def embed(self, texts: Sequence[str], *, record: UsageRecorder) -> list[list[float]]:
        model = f"models/{self._client.provider.model}"
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH):
            batch = texts[start : start + MAX_BATCH]
            body = {
                "requests": [{"model": model, "content": {"parts": [{"text": t}]}} for t in batch]
            }
            result = await self._client.generate(body, record=record, method="batchEmbedContents")
            embeddings = result.get("embeddings") or []
            if len(embeddings) != len(batch):
                raise ValueError("the embeddings response does not match the request")
            vectors += [[float(value) for value in item["values"]] for item in embeddings]
        return vectors


def gemini_embedding_factory(
    http: httpx.AsyncClient, quota: QuotaGuard
) -> Callable[[ProviderSnapshot, str], GeminiEmbeddingProvider]:
    def build(provider: ProviderSnapshot, api_key: str) -> GeminiEmbeddingProvider:
        return GeminiEmbeddingProvider(GeminiClient(http, api_key, provider, quota=quota))

    return build
