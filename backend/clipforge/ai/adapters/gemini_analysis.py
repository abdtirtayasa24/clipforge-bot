"""Gemini viral analysis with JSON-schema structured output (ADR-011, ADR-027)."""

import asyncio
import base64
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from clipforge.ai.adapters.gemini_client import GeminiClient
from clipforge.ai.adapters.gemini_transcription import response_text
from clipforge.ai.prompts.analysis import (
    MAX_OUTPUT_TOKENS,
    PROPOSAL_SCHEMA,
    RESCORE_SCHEMA,
    SYSTEM_PROMPT,
    OutputCutOff,
    Repair,
    parse_proposals,
    parse_rescores,
    repair_prompt,
    rerank_prompt,
    window_prompt,
    with_repair,
)
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.types import AnalysisContext, ClipCandidate, Frame, Segment, TranscriptResult
from clipforge.ai.usage import UsageRecorder
from clipforge.ai.windowing import excerpt
from clipforge.settings_schema import ProviderSnapshot

TEMPERATURE = 0.4


async def image_parts(frames: Sequence[Frame]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for frame in frames:
        data = await asyncio.to_thread(frame.path.read_bytes)
        parts.append({"text": f"Frame at {frame.time_s:.1f} s:"})
        parts.append(
            {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(data).decode()}}
        )
    return parts


class GeminiAnalysisProvider:
    def __init__(self, client: GeminiClient) -> None:
        self._client = client

    async def _ask(
        self,
        parts: list[dict[str, Any]],
        schema: dict[str, Any],
        record: UsageRecorder,
        repair: Repair | None,
    ) -> str:
        contents: list[dict[str, Any]] = [{"role": "user", "parts": parts}]
        if repair is not None:
            contents += [
                {"role": "model", "parts": [{"text": repair.previous_answer}]},
                {"role": "user", "parts": [{"text": repair_prompt(repair.error)}]},
            ]
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "temperature": TEMPERATURE,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
            },
        }
        result = await self._client.generate(body, record=record)
        if any(c.get("finishReason") == "MAX_TOKENS" for c in result.get("candidates") or []):
            raise OutputCutOff
        return response_text(result)

    async def propose(
        self, segments: Sequence[Segment], context: AnalysisContext, *, record: UsageRecorder
    ) -> list[ClipCandidate]:
        parts = [{"text": window_prompt(segments, context)}]
        return await with_repair(
            lambda repair: self._ask(parts, PROPOSAL_SCHEMA, record, repair), parse_proposals
        )

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
        parts = [{"text": rerank_prompt(candidates, excerpts, context)}, *await image_parts(frames)]
        return await with_repair(
            lambda repair: self._ask(parts, RESCORE_SCHEMA, record, repair),
            lambda text: parse_rescores(text, candidates),
        )


def gemini_analysis_factory(
    http: httpx.AsyncClient, quota: QuotaGuard
) -> Callable[[ProviderSnapshot, str], GeminiAnalysisProvider]:
    def build(provider: ProviderSnapshot, api_key: str) -> GeminiAnalysisProvider:
        return GeminiAnalysisProvider(GeminiClient(http, api_key, provider, quota=quota))

    return build
