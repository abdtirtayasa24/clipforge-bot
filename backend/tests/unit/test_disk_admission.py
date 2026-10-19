import pytest

from clipforge.ingest.disk import (
    AUDIO_BYTES_PER_SECOND,
    CLIPS_ESTIMATE_BYTES,
    Admission,
    DiskUsage,
    decide_admission,
    projected_usage,
)

GIB = 1024**3
RESERVE = 5 * GIB


def test_projection_counts_source_audio_and_clips() -> None:
    assert projected_usage(GIB, duration_s=3600) == (
        GIB + 3600 * AUDIO_BYTES_PER_SECOND + CLIPS_ESTIMATE_BYTES
    )


def test_unknown_duration_assumes_the_longest_source() -> None:
    assert projected_usage(GIB, duration_s=None) == projected_usage(GIB, duration_s=7200)


def test_admits_when_reserve_stays_free() -> None:
    usage = DiskUsage(total=30 * GIB, free=10 * GIB)

    decision = decide_admission(2 * GIB, duration_s=3600, usage=usage, reserve_bytes=RESERVE)

    assert decision.verdict is Admission.ADMIT
    assert decision.projected_bytes == projected_usage(2 * GIB, duration_s=3600)


def test_defers_when_free_space_would_breach_the_reserve() -> None:
    usage = DiskUsage(total=30 * GIB, free=6 * GIB)

    decision = decide_admission(2 * GIB, duration_s=3600, usage=usage, reserve_bytes=RESERVE)

    assert decision.verdict is Admission.DEFER


def test_rejects_what_could_never_fit() -> None:
    usage = DiskUsage(total=20 * GIB, free=20 * GIB)

    decision = decide_admission(16 * GIB, duration_s=3600, usage=usage, reserve_bytes=RESERVE)

    assert decision.verdict is Admission.REJECT


@pytest.mark.parametrize("source_bytes", [0, -1])
def test_source_size_must_be_positive(source_bytes: int) -> None:
    with pytest.raises(ValueError):
        projected_usage(source_bytes, duration_s=60)
