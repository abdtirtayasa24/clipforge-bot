"""OpenRouter fallback analysis over the OpenAI-compatible chat API (ADR-027).

Free models may lack JSON mode, so the JSON shape is enforced by the prompt plus Pydantic
validation with one repair turn.
"""

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

import httpx

from clipforge.ai.errors import ModelUnavailable
from clipforge.ai.prompts.analysis import (
    PROPOSAL_SCHEMA,
    RESCORE_SCHEMA,
    SYSTEM_PROMPT,
    Repair,
    parse_proposals,
    parse_rescores,
    repair_prompt,
    rerank_prompt,
    schema_hint,
    window_prompt,
    with_repair,
)
from clipforge.ai.quota import QuotaGuard, retry_after_seconds
from clipforge.ai.registry import ProviderConfigError
from clipforge.ai.types import AnalysisContext, ClipCandidate, Frame, Segment, TranscriptResult
from clipforge.ai.usage import ProviderCall, UsageRecorder
from clipforge.ai.windowing import excerpt
from clipforge.queue.errors import PermanentError
from clipforge.settings_schema import ProviderSnapshot

logger = logging.getLogger(__name__)

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
APP_TITLE = "ClipForge Bot"
MAX_ATTEMPTS = 3
RETRY_DELAYS_S = (2.0, 8.0)
MAX_INLINE_RATE_LIMIT_WAIT_S = 60.0
REQUEST_TIMEOUT = httpx.Timeout(30.0, read=300.0)
TEMPERATURE = 0.4
_RETRYABLE_STATUSES = frozenset({408, 500, 502, 503, 504})


def rate_limit_reset_wait(headers: httpx.Headers, now: datetime) -> float | None:
    """Seconds until `X-RateLimit-Reset` (epoch milliseconds or seconds) when nothing is left."""
    if headers.get("x-ratelimit-remaining") != "0":
        return None
    try:
        reset = float(headers.get("x-ratelimit-reset", ""))
    except ValueError:
        return None
    reset_s = reset / 1000 if reset > 1e11 else reset
    return max(0.0, reset_s - now.timestamp())


class OpenRouterClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        provider: ProviderSnapshot,
        *,
        quota: QuotaGuard,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {api_key}", "X-Title": APP_TITLE}
        self.provider = provider
        self._quota = quota
        self._sleep = sleep

    async def chat(self, messages: list[dict[str, Any]], *, record: UsageRecorder) -> str:
        body = {"model": self.provider.model, "messages": messages, "temperature": TEMPERATURE}
        model = self.provider.model
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._quota.acquire(self.provider)
            started = time.monotonic()
            try:
                response = await self._http.post(
                    CHAT_URL, json=body, headers=self._headers, timeout=REQUEST_TIMEOUT
                )
            except httpx.TransportError as exc:
                await record(self._call(started, False, error_code=type(exc).__name__))
                error: Exception = ModelUnavailable(f"{model} request failed: {type(exc).__name__}")
                wait: float | None = RETRY_DELAYS_S[min(attempt, 2) - 1]
            else:
                if response.is_success:
                    return await self._success(response, started, record)
                await record(self._call(started, False, error_code=str(response.status_code)))
                error, wait = self._error(response, attempt)
            if wait is None or attempt == MAX_ATTEMPTS:
                raise error
            logger.warning(
                "openrouter request failed; retrying",
                extra={"model": model, "attempt": attempt, "wait_s": wait},
            )
            await self._sleep(wait)
        raise AssertionError("unreachable")

    async def _success(
        self, response: httpx.Response, started: float, record: UsageRecorder
    ) -> str:
        result = response.json()
        usage = result.get("usage") or {}
        await record(
            self._call(
                started,
                True,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
            )
        )
        if "error" in result:
            # Upstream failures can arrive inside a 200 response.
            raise ModelUnavailable(f"{self.provider.model} failed: {result['error'].get('code')}")
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        return content if isinstance(content, str) else ""

    def _error(self, response: httpx.Response, attempt: int) -> tuple[Exception, float | None]:
        model, code = self.provider.model, response.status_code
        if code == 429:
            now = self._quota.now()
            wait = retry_after_seconds(response.headers, now)
            if wait is None:
                wait = rate_limit_reset_wait(response.headers, now)
            deferral = self._quota.deferral(self.provider, wait)
            short = wait is not None and wait <= MAX_INLINE_RATE_LIMIT_WAIT_S
            return deferral, wait if short else None
        if code in _RETRYABLE_STATUSES:
            return ModelUnavailable(f"{model} returned {code}"), RETRY_DELAYS_S[min(attempt, 2) - 1]
        if code == 404:
            return ModelUnavailable(f"{model} is not available"), None
        if code in (401, 402):
            return ProviderConfigError(f"OpenRouter rejected the key or account ({code})"), None
        return PermanentError(f"{model} rejected the request ({code})"), None

    def _call(self, started: float, success: bool, **fields: Any) -> ProviderCall:
        return ProviderCall(
            success=success, latency_ms=int((time.monotonic() - started) * 1000), **fields
        )


async def image_content(frames: Sequence[Frame]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for frame in frames:
        data = base64.b64encode(await asyncio.to_thread(frame.path.read_bytes)).decode()
        content.append({"type": "text", "text": f"Frame at {frame.time_s:.1f} s:"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}
        )
    return content


class OpenRouterAnalysisProvider:
    def __init__(self, client: OpenRouterClient) -> None:
        self._client = client

    async def _ask(
        self, content: list[dict[str, Any]], record: UsageRecorder, repair: Repair | None
    ) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        if repair is not None:
            messages += [
                {"role": "assistant", "content": repair.previous_answer},
                {"role": "user", "content": repair_prompt(repair.error)},
            ]
        return await self._client.chat(messages, record=record)

    async def propose(
        self, segments: Sequence[Segment], context: AnalysisContext, *, record: UsageRecorder
    ) -> list[ClipCandidate]:
        text = f"{window_prompt(segments, context)}\n\n{schema_hint(PROPOSAL_SCHEMA)}"
        content = [{"type": "text", "text": text}]
        return await with_repair(lambda repair: self._ask(content, record, repair), parse_proposals)

    async def rerank(
        self,
        candidates: Sequence[ClipCandidate],
        transcript: TranscriptResult,
        frames: Sequence[Frame],
        context: AnalysisContext,
        *,
        record: UsageRecorder,
    ) -> list[ClipCandidate]:
        excerpts = [excerpt(transcript, candidate) for candidate in candidates]
        text = f"{rerank_prompt(candidates, excerpts, context)}\n\n{schema_hint(RESCORE_SCHEMA)}"
        content = [{"type": "text", "text": text}, *await image_content(frames)]
        return await with_repair(
            lambda repair: self._ask(content, record, repair),
            lambda answer: parse_rescores(answer, candidates),
        )


def openrouter_analysis_factory(
    http: httpx.AsyncClient, quota: QuotaGuard
) -> Callable[[ProviderSnapshot, str], OpenRouterAnalysisProvider]:
    def build(provider: ProviderSnapshot, api_key: str) -> OpenRouterAnalysisProvider:
        return OpenRouterAnalysisProvider(OpenRouterClient(http, api_key, provider, quota=quota))

    return build
