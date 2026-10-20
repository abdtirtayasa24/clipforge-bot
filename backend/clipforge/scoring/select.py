"""Pure candidate selection: threshold, snap, clamp, overlap limit, top N (ADR-010)."""

import bisect
import dataclasses
from collections.abc import Sequence

from clipforge.ai.types import TimeSpan, TranscriptResult
from clipforge.scoring.score import ScoredCandidate
from clipforge.settings_schema import SettingsSchema

EPSILON_S = 1e-6


def overlap_ratio(a: TimeSpan, b: TimeSpan) -> float:
    """Shared time as a fraction of the shorter clip."""
    shared = min(a.end_s, b.end_s) - max(a.start_s, b.start_s)
    shorter = min(a.end_s - a.start_s, b.end_s - b.start_s)
    return max(0.0, shared) / shorter if shorter > 0 else 0.0


def _nearest(values: Sequence[float], target: float) -> float:
    index = bisect.bisect_left(values, target)
    neighbours = values[max(0, index - 1) : index + 1]
    return min(neighbours, key=lambda value: abs(value - target))


def _fit(
    start: float, end: float, ends: Sequence[float], settings: SettingsSchema
) -> tuple[float, float] | None:
    """Moves `end` to a boundary that gives a duration within range, or None if none does."""
    low, high = start + settings.clip_min_s - EPSILON_S, start + settings.clip_max_s + EPSILON_S
    if low <= end <= high:
        return start, end
    if not ends:
        return (start, start + settings.clip_max_s) if end > high else None
    if end > high:
        index = bisect.bisect_right(ends, high) - 1
        candidate = ends[index] if index >= 0 else None
    else:
        index = bisect.bisect_left(ends, low)
        candidate = ends[index] if index < len(ends) else None
    if candidate is None or not low <= candidate <= high:
        return None
    return start, candidate


def _snap(
    candidate: ScoredCandidate, starts: Sequence[float], ends: Sequence[float]
) -> tuple[float, float]:
    start, end = candidate.candidate.start_s, candidate.candidate.end_s
    if not starts:
        return start, end
    start = _nearest(starts, start)
    later_ends = ends[bisect.bisect_right(ends, start + EPSILON_S) :]
    return start, _nearest(later_ends, end) if later_ends else end


def select(
    candidates: Sequence[ScoredCandidate],
    transcript: TranscriptResult,
    settings: SettingsSchema,
) -> list[ScoredCandidate]:
    """Best non-overlapping candidates; `rank` 1 is the highest score."""
    starts = sorted({segment.start_s for segment in transcript.segments})
    ends = sorted({segment.end_s for segment in transcript.segments})
    eligible = [c for c in candidates if c.total >= settings.min_score]
    eligible.sort(key=lambda c: (-c.total, c.candidate.start_s))

    chosen: list[ScoredCandidate] = []
    for scored in eligible:
        if len(chosen) == settings.max_clips:
            break
        start, end = _snap(scored, starts, ends)
        fitted = _fit(start, end, ends, settings)
        if fitted is None:
            continue
        clip = scored.candidate.model_copy(update={"start_s": fitted[0], "end_s": fitted[1]})
        if any(overlap_ratio(clip, kept.candidate) > settings.max_overlap for kept in chosen):
            continue
        chosen.append(dataclasses.replace(scored, candidate=clip, rank=len(chosen) + 1))
    return chosen
