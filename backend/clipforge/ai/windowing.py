"""Windowed analysis of long transcripts with provider fallback (ADR-011, ADR-027, ADR-028)."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from clipforge.ai.errors import ModelUnavailable
from clipforge.ai.protocols import ViralAnalysisProvider
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.types import (
    AnalysisContext,
    ClipCandidate,
    Frame,
    Segment,
    TimeSpan,
    TranscriptResult,
)
from clipforge.ai.usage import UsageRecorder
from clipforge.queue.errors import PermanentError, QuotaDeferred
from clipforge.scoring.score import score
from clipforge.settings_schema import ProviderSnapshot

logger = logging.getLogger(__name__)

WINDOW_S = 540.0
WINDOW_OVERLAP_S = 60.0
# Proposals from neighbouring windows overlapping this much are the same moment.
DUPLICATE_OVERLAP = 0.5
FRAMES_PER_CANDIDATE = 4
MAX_RERANK_FRAMES = 24


def windows(
    transcript: TranscriptResult, *, window_s: float = WINDOW_S, overlap_s: float = WINDOW_OVERLAP_S
) -> list[list[Segment]]:
    """Overlapping ~9 minute windows of whole segments, so clips at a window edge are seen."""
    if not transcript.segments:
        return []
    end = transcript.segments[-1].end_s
    step = window_s - overlap_s
    result: list[list[Segment]] = []
    start = 0.0
    while True:
        window = [s for s in transcript.segments if start <= s.start_s < start + window_s]
        if window:
            result.append(window)
        if start + window_s >= end:
            return result
        start += step


def excerpt(transcript: TranscriptResult, span: TimeSpan) -> list[Segment]:
    return [s for s in transcript.segments if s.end_s > span.start_s and s.start_s < span.end_s]


def clamp(candidate: ClipCandidate, duration_s: float) -> ClipCandidate | None:
    """Keeps model times inside the media; candidates with nothing left are dropped."""
    start, end = max(0.0, candidate.start_s), min(duration_s, candidate.end_s)
    if end <= start:
        return None
    return candidate.model_copy(update={"start_s": start, "end_s": end})


def _overlap(a: TimeSpan, b: TimeSpan) -> float:
    shared = min(a.end_s, b.end_s) - max(a.start_s, b.start_s)
    shorter = min(a.end_s - a.start_s, b.end_s - b.start_s)
    return max(0.0, shared) / shorter if shorter > 0 else 0.0


def ranked(candidates: Sequence[ClipCandidate]) -> list[ClipCandidate]:
    """Best first by server-side score, without near-duplicates."""
    kept: list[ClipCandidate] = []
    for candidate in sorted(candidates, key=lambda c: (-score(c.scores), c.start_s)):
        if all(_overlap(candidate, other) <= DUPLICATE_OVERLAP for other in kept):
            kept.append(candidate)
    return kept


def frames_for(candidates: Sequence[ClipCandidate], frames: Sequence[Frame]) -> list[Frame]:
    """A few frames inside each shortlisted range, nearest to the middle if none is inside."""
    chosen: dict[float, Frame] = {}
    per_candidate = max(1, min(FRAMES_PER_CANDIDATE, MAX_RERANK_FRAMES // max(1, len(candidates))))
    for candidate in candidates:
        inside = [f for f in frames if candidate.start_s <= f.time_s <= candidate.end_s]
        if not inside and frames:
            middle = (candidate.start_s + candidate.end_s) / 2
            inside = [min(frames, key=lambda f: abs(f.time_s - middle))]
        step = max(1, len(inside) // per_candidate)
        for frame in inside[::step][:per_candidate]:
            chosen[frame.time_s] = frame
    return sorted(chosen.values(), key=lambda frame: frame.time_s)[:MAX_RERANK_FRAMES]


@dataclass(frozen=True)
class ProviderChoice:
    snapshot: ProviderSnapshot
    provider: ViralAnalysisProvider


@dataclass
class AnalysisOutcome:
    proposals: list[ClipCandidate]
    shortlist: list[ClipCandidate]
    window_models: list[str] = field(default_factory=list)
    rerank_model: str | None = None
    frames_sent: int = 0


RecorderFor = Callable[[ProviderSnapshot], UsageRecorder]


class AllProvidersUnavailable(Exception):
    pass


class Analyzer:
    def __init__(self, quota: QuotaGuard, recorder_for: RecorderFor) -> None:
        self._quota = quota
        self._recorder_for = recorder_for

    async def _reserve_first(self, chain: Sequence[ProviderChoice], start: int, calls: int) -> int:
        """Index of the first provider from `start` with quota for `calls` requests."""
        deferrals: list[QuotaDeferred] = []
        for index in range(start, len(chain)):
            try:
                await self._quota.reserve(chain[index].snapshot, calls)
            except QuotaDeferred as exc:
                deferrals.append(exc)
                continue
            except PermanentError as exc:
                logger.warning("provider cannot fit the request", extra={"reason": str(exc)})
                continue
            return index
        if deferrals:
            soonest = min(deferrals, key=lambda exc: exc.until)
            raise QuotaDeferred("no analysis provider has quota left", until=soonest.until)
        raise AllProvidersUnavailable

    async def propose_all(
        self,
        transcript: TranscriptResult,
        context: AnalysisContext,
        chain: Sequence[ProviderChoice],
    ) -> tuple[list[ClipCandidate], list[str]]:
        parts = windows(transcript)
        proposals: list[ClipCandidate] = []
        models: list[str] = []
        index = await self._reserve_first(chain, 0, len(parts))
        position = 0
        while position < len(parts):
            choice = chain[index]
            try:
                found = await choice.provider.propose(
                    parts[position], context, record=self._recorder_for(choice.snapshot)
                )
            except (QuotaDeferred, ModelUnavailable) as exc:
                logger.warning(
                    "analysis provider unavailable; trying the next one",
                    extra={"model": choice.snapshot.model, "reason": str(exc)},
                )
                try:
                    index = await self._reserve_first(chain, index + 1, len(parts) - position)
                except AllProvidersUnavailable:
                    raise exc from None
                continue
            proposals += [c for c in (clamp(c, context.duration_s) for c in found) if c]
            models.append(choice.snapshot.model)
            position += 1
        return proposals, models

    async def rerank(
        self,
        shortlist: list[ClipCandidate],
        transcript: TranscriptResult,
        frames: Sequence[Frame],
        context: AnalysisContext,
        chain: Sequence[ProviderChoice],
    ) -> tuple[list[ClipCandidate], str]:
        index = await self._reserve_first(chain, 0, 1)
        while True:
            choice = chain[index]
            try:
                rescored = await choice.provider.rerank(
                    shortlist,
                    transcript,
                    frames,
                    context,
                    record=self._recorder_for(choice.snapshot),
                )
                return rescored, choice.snapshot.model
            except (QuotaDeferred, ModelUnavailable) as exc:
                logger.warning(
                    "re-rank provider unavailable; trying the next one",
                    extra={"model": choice.snapshot.model, "reason": str(exc)},
                )
                try:
                    index = await self._reserve_first(chain, index + 1, 1)
                except AllProvidersUnavailable:
                    raise exc from None

    async def analyze(
        self,
        transcript: TranscriptResult,
        context: AnalysisContext,
        frames: Sequence[Frame],
        *,
        window_chain: Sequence[ProviderChoice],
        rerank_chain: Sequence[ProviderChoice],
        shortlist_size: int,
    ) -> AnalysisOutcome:
        proposals, window_models = await self.propose_all(transcript, context, window_chain)
        shortlist = ranked(proposals)[:shortlist_size]
        if not shortlist:
            return AnalysisOutcome(proposals, [], window_models)
        sent = frames_for(shortlist, frames)
        rescored, rerank_model = await self.rerank(
            shortlist, transcript, sent, context, rerank_chain
        )
        return AnalysisOutcome(
            proposals=proposals,
            shortlist=ranked(rescored),
            window_models=window_models,
            rerank_model=rerank_model,
            frames_sent=len(sent),
        )
