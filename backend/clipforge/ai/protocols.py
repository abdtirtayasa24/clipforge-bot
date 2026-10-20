"""Capability protocols every AI adapter implements (spec "AI provider interfaces", ADR-009).

Adapters report each request, retries included, through `record` (ADR-024).
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from clipforge.ai.types import AnalysisContext, ClipCandidate, Frame, Segment, TranscriptResult
from clipforge.ai.usage import UsageRecorder


class TranscriptionProvider(Protocol):
    async def transcribe(
        self,
        audio_path: Path,
        *,
        duration_s: float,
        language_hint: str | None,
        record: UsageRecorder,
    ) -> TranscriptResult:
        """Times in the result are relative to the start of `audio_path`."""
        ...


class ViralAnalysisProvider(Protocol):
    async def propose(
        self, segments: Sequence[Segment], context: AnalysisContext, *, record: UsageRecorder
    ) -> list[ClipCandidate]:
        """Candidates from one transcript window (ADR-011)."""
        ...

    async def rerank(
        self,
        candidates: Sequence[ClipCandidate],
        transcript: TranscriptResult,
        frames: Sequence[Frame],
        context: AnalysisContext,
        *,
        record: UsageRecorder,
    ) -> list[ClipCandidate]:
        """Re-scores the shortlist, using frames for the visual dimension."""
        ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str], *, record: UsageRecorder) -> list[list[float]]: ...
