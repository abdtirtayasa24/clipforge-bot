import pytest

from clipforge.media.audio import ChunkPlan, plan_chunks


def spans(plans: list[ChunkPlan]) -> list[tuple[float, float]]:
    return [(plan.start_s, plan.end_s) for plan in plans]


@pytest.mark.parametrize(
    ("duration_s", "chunk_s", "expected"),
    [
        (1500, 600, [(0, 602), (600, 1202), (1200, 1500)]),
        (1200, 600, [(0, 602), (600, 1200)]),
        (1201.5, 600, [(0, 602), (600, 1201.5)]),
        (300, 600, [(0, 300)]),
        (7200, 1200, [(i * 1200, min(7200, (i + 1) * 1200 + 2)) for i in range(6)]),
    ],
)
def test_chunks_overlap_and_cover_the_whole_duration(
    duration_s: float, chunk_s: float, expected: list[tuple[float, float]]
) -> None:
    plans = plan_chunks(duration_s, chunk_s=chunk_s)

    assert spans(plans) == expected
    assert [plan.index for plan in plans] == list(range(len(expected)))


def test_chunk_length_is_the_planned_span() -> None:
    assert plan_chunks(1500, chunk_s=600)[2].duration_s == 300


@pytest.mark.parametrize("chunk_s", [2, 28 * 60 + 1])
def test_chunk_length_outside_bounds_is_rejected(chunk_s: float) -> None:
    with pytest.raises(ValueError, match="chunk"):
        plan_chunks(1500, chunk_s=chunk_s)


def test_empty_media_is_rejected() -> None:
    with pytest.raises(ValueError, match="duration"):
        plan_chunks(0, chunk_s=600)
