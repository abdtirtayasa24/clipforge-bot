"""Deterministic providers for tests and dry runs (ADR-009); usage is reported like a real call."""

from collections.abc import Callable, Sequence
from pathlib import Path

from clipforge.ai.types import (
    AnalysisContext,
    ClipCandidate,
    Frame,
    Segment,
    TranscriptResult,
    Word,
)
from clipforge.ai.usage import ProviderCall, UsageRecorder

FAKE_LATENCY_MS = 5
TOKENS_PER_WORD = 2


def spoken_transcript(duration_s: float, *, sentence_s: float = 5.0) -> TranscriptResult:
    """One short Indonesian sentence every `sentence_s` seconds, with word timings."""
    segments = []
    start = 0.0
    index = 0
    while start + sentence_s <= duration_s:
        texts = ["ini", "kalimat", "nomor", str(index)]
        step = sentence_s / len(texts)
        words = [
            Word(text=text, start_s=start + i * step, end_s=start + (i + 1) * step)
            for i, text in enumerate(texts)
        ]
        segments.append(
            Segment(text=" ".join(texts), start_s=start, end_s=start + sentence_s, words=words)
        )
        start += sentence_s
        index += 1
    return TranscriptResult(language="id", segments=segments)


class FakeTranscriptionProvider:
    def __init__(
        self, transcript_for: Callable[[float], TranscriptResult] = spoken_transcript
    ) -> None:
        self._transcript_for = transcript_for
        self.calls: list[Path] = []

    async def transcribe(
        self,
        audio_path: Path,
        *,
        duration_s: float,
        language_hint: str | None,
        record: UsageRecorder,
    ) -> TranscriptResult:
        self.calls.append(audio_path)
        await record(
            ProviderCall(success=True, latency_ms=FAKE_LATENCY_MS, audio_seconds=duration_s)
        )
        return self._transcript_for(duration_s)


def _tokens(segments: Sequence[Segment]) -> int:
    return sum(len(segment.text.split()) for segment in segments) * TOKENS_PER_WORD


class FakeAnalysisProvider:
    """Proposes the configured candidates that fall inside each window."""

    def __init__(self, candidates: Sequence[ClipCandidate]) -> None:
        self._candidates = list(candidates)
        self.windows: list[tuple[float, float]] = []
        self.reranked_frames: list[Frame] = []

    async def propose(
        self, segments: Sequence[Segment], context: AnalysisContext, *, record: UsageRecorder
    ) -> list[ClipCandidate]:
        if not segments:
            return []
        start, end = segments[0].start_s, segments[-1].end_s
        self.windows.append((start, end))
        await record(
            ProviderCall(
                success=True,
                latency_ms=FAKE_LATENCY_MS,
                input_tokens=_tokens(segments),
                output_tokens=100,
            )
        )
        return [c for c in self._candidates if start <= c.start_s and c.end_s <= end]

    async def rerank(
        self,
        candidates: Sequence[ClipCandidate],
        transcript: TranscriptResult,
        frames: Sequence[Frame],
        context: AnalysisContext,
        *,
        record: UsageRecorder,
    ) -> list[ClipCandidate]:
        self.reranked_frames.extend(frames)
        await record(
            ProviderCall(
                success=True,
                latency_ms=FAKE_LATENCY_MS,
                input_tokens=_tokens(transcript.segments),
                output_tokens=100,
            )
        )
        return list(candidates)
