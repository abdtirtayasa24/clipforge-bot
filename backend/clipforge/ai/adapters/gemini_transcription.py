"""Gemini transcription (ADR-027): `gemini-3.5-transcribe` with word timestamps, and a
segment-level fallback through a multimodal model when the transcribe model is unavailable."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from clipforge.ai.adapters.gemini_client import GeminiClient
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.timestamps import parse_offset, segments_from_words
from clipforge.ai.types import Segment, TranscriptResult, Word
from clipforge.ai.usage import UsageRecorder
from clipforge.queue.errors import TransientError
from clipforge.settings_schema import ProviderSnapshot

AUDIO_MIME_TYPE = "audio/mp3"

FALLBACK_PROMPT = (
    "Transcribe this audio verbatim in the language that is spoken (often Indonesian, possibly "
    "mixed with English). Do not translate or summarize. Split the transcript into sentences and "
    "give each sentence's start and end time in seconds from the start of the audio. Return "
    "language as a BCP-47 code."
)
FALLBACK_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "language": {"type": "STRING"},
        "segments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start_s": {"type": "NUMBER"},
                    "end_s": {"type": "NUMBER"},
                    "text": {"type": "STRING"},
                },
                "required": ["start_s", "end_s", "text"],
            },
        },
    },
    "required": ["segments"],
}


def _words(body: dict[str, Any]) -> tuple[list[Word], str | None]:
    """Words in spoken order; times that go backwards are clamped forward (the model does that)."""
    words: list[Word] = []
    language = None
    previous_start = 0.0
    for candidate in body.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            transcription = part.get("audioTranscription") or {}
            language = language or transcription.get("languageCode")
            for item in transcription.get("words") or []:
                start, end = (
                    parse_offset(item.get("startOffset")),
                    parse_offset(item.get("endOffset")),
                )
                text = str(item.get("word") or "").strip()
                if text and start is not None and end is not None and end >= start >= 0:
                    previous_start = max(start, previous_start)
                    words.append(
                        Word(text=text, start_s=previous_start, end_s=max(end, previous_start))
                    )
        break
    return words, language


class GeminiTranscriptionProvider:
    def __init__(self, client: GeminiClient) -> None:
        self._client = client

    async def transcribe(
        self,
        audio_path: Path,
        *,
        duration_s: float,
        language_hint: str | None,
        record: UsageRecorder,
    ) -> TranscriptResult:
        config: dict[str, Any] = {"wordTimestamp": True, "diarization": False}
        if language_hint:
            config["languageCodes"] = [language_hint]
        body = {
            "contents": [{"parts": [await self._client.audio_part(audio_path, AUDIO_MIME_TYPE)]}],
            "generationConfig": {"audioTranscriptionConfig": config},
        }
        result = await self._client.generate(body, record=record, audio_seconds=duration_s)
        words, language = _words(result)
        return TranscriptResult(language=language, segments=segments_from_words(words))


class _FallbackSegment(BaseModel):
    start_s: float
    end_s: float
    text: str


class _FallbackTranscript(BaseModel):
    language: str | None = None
    segments: list[_FallbackSegment]


def response_text(body: dict[str, Any]) -> str:
    for candidate in body.get("candidates") or []:
        parts = (candidate.get("content") or {}).get("parts") or []
        return "".join(str(part.get("text", "")) for part in parts)
    return ""


class GeminiAudioSegmentsProvider:
    """Fallback transcription with segment-level timing only."""

    def __init__(self, client: GeminiClient) -> None:
        self._client = client

    async def transcribe(
        self,
        audio_path: Path,
        *,
        duration_s: float,
        language_hint: str | None,
        record: UsageRecorder,
    ) -> TranscriptResult:
        body = {
            "contents": [
                {
                    "parts": [
                        await self._client.audio_part(audio_path, AUDIO_MIME_TYPE),
                        {"text": FALLBACK_PROMPT},
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": FALLBACK_SCHEMA,
            },
        }
        result = await self._client.generate(body, record=record, audio_seconds=duration_s)
        try:
            parsed = _FallbackTranscript.model_validate_json(response_text(result))
        except ValidationError:
            raise TransientError("fallback transcription returned malformed JSON") from None
        segments = [
            Segment(
                text=item.text.strip(),
                start_s=min(max(item.start_s, 0.0), duration_s),
                end_s=min(max(item.end_s, item.start_s, 0.0), duration_s),
            )
            for item in parsed.segments
            if item.text.strip() and item.start_s < duration_s
        ]
        segments.sort(key=lambda segment: segment.start_s)
        return TranscriptResult(language=parsed.language, segments=segments)


def gemini_transcription_factory(
    http: httpx.AsyncClient, quota: QuotaGuard
) -> Callable[[ProviderSnapshot, str], GeminiTranscriptionProvider]:
    def build(provider: ProviderSnapshot, api_key: str) -> GeminiTranscriptionProvider:
        return GeminiTranscriptionProvider(GeminiClient(http, api_key, provider, quota=quota))

    return build


def gemini_audio_segments_factory(
    http: httpx.AsyncClient, quota: QuotaGuard
) -> Callable[[ProviderSnapshot, str], GeminiAudioSegmentsProvider]:
    def build(provider: ProviderSnapshot, api_key: str) -> GeminiAudioSegmentsProvider:
        return GeminiAudioSegmentsProvider(GeminiClient(http, api_key, provider, quota=quota))

    return build
