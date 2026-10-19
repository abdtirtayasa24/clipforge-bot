"""Disk admission: a new source must never push free space below the reserve (spec)."""

from dataclasses import dataclass
from enum import StrEnum

from clipforge.ingest.validation import MAX_DURATION_S

# Mono 16 kHz compressed audio (ADR-012) at about 64 kbit/s.
AUDIO_BYTES_PER_SECOND = 8_000
# Three 60 s clips at 720x1280 (ADR-013), about 5 Mbit/s each, plus subtitles.
CLIPS_ESTIMATE_BYTES = 3 * 60 * 625_000 + 1_000_000


class Admission(StrEnum):
    ADMIT = "admit"
    DEFER = "defer"
    REJECT = "reject"


@dataclass(frozen=True)
class DiskUsage:
    total: int
    free: int


@dataclass(frozen=True)
class AdmissionDecision:
    verdict: Admission
    projected_bytes: int
    free_bytes: int
    reserve_bytes: int


def projected_usage(source_bytes: int, *, duration_s: float | None) -> int:
    """Workspace bytes a source needs until its clips are uploaded; unknown duration = longest."""
    if source_bytes <= 0:
        raise ValueError("source size must be positive")
    duration = MAX_DURATION_S if duration_s is None else duration_s
    return source_bytes + int(duration * AUDIO_BYTES_PER_SECOND) + CLIPS_ESTIMATE_BYTES


def decide_admission(
    source_bytes: int, *, duration_s: float | None, usage: DiskUsage, reserve_bytes: int
) -> AdmissionDecision:
    """Admit if it fits now, defer if it fits once other work is cleaned up, else reject."""
    projected = projected_usage(source_bytes, duration_s=duration_s)
    if projected <= usage.free - reserve_bytes:
        verdict = Admission.ADMIT
    elif projected <= usage.total - reserve_bytes:
        verdict = Admission.DEFER
    else:
        verdict = Admission.REJECT
    return AdmissionDecision(verdict, projected, usage.free, reserve_bytes)
