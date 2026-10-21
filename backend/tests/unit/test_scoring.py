import random
from decimal import Decimal

import pytest

from clipforge.ai.types import ClipCandidate, DimensionScores, Segment, TranscriptResult
from clipforge.scoring.score import ScoredCandidate, score
from clipforge.scoring.select import overlap_ratio, select
from clipforge.settings_schema import SettingsSchema

DEFAULTS = SettingsSchema()


def dims(h: int = 0, e: int = 0, c: int = 0, r: int = 0, v: int = 0, f: int = 0) -> DimensionScores:
    return DimensionScores(hook=h, emotion=e, clarity=c, relevance=r, visual=v, completeness=f)


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        (dims(100, 100, 100, 100, 100, 100), "100"),
        (dims(), "0"),
        (dims(h=100), "25"),
        (dims(e=100), "20"),
        (dims(c=100), "20"),
        (dims(r=100), "15"),
        (dims(v=100), "10"),
        (dims(f=100), "10"),
        (dims(80, 70, 90, 60, 50, 75), "73.5"),
        (dims(75, 75, 75, 75, 75, 75), "75"),
        (dims(75, 75, 75, 75, 75, 74), "74.9"),
        (dims(91, 83, 77, 69, 58, 99), "80.8"),
    ],
)
def test_score_uses_the_spec_weights(scores: DimensionScores, expected: str) -> None:
    assert score(scores) == Decimal(expected)


@pytest.mark.parametrize("bad", [-1, 101])
def test_dimension_scores_are_bounded(bad: int) -> None:
    with pytest.raises(ValueError):
        dims(h=bad)


def transcript(duration_s: float = 600, step_s: float = 0.5) -> TranscriptResult:
    """Fine-grained sentence boundaries so snapping barely moves the test ranges."""
    count = int(duration_s / step_s)
    return TranscriptResult(
        segments=[
            Segment(text=f"s{i}", start_s=i * step_s, end_s=(i + 1) * step_s) for i in range(count)
        ]
    )


def scored(start: float, end: float, total: str) -> ScoredCandidate:
    candidate = ClipCandidate(start_s=start, end_s=end, scores=dims(), title="t", hook_line="h")
    return ScoredCandidate(candidate=candidate, total=Decimal(total))


def spans(selected: list[ScoredCandidate]) -> list[tuple[float, float]]:
    return [(s.candidate.start_s, s.candidate.end_s) for s in selected]


@pytest.mark.parametrize(("total", "kept"), [("74.99", False), ("75.00", True), ("75.01", True)])
def test_threshold_edge(total: str, kept: bool) -> None:
    selected = select([scored(10, 40, total)], transcript(), DEFAULTS)

    assert bool(selected) is kept


@pytest.mark.parametrize(
    ("second_start", "kept"),
    [
        # Both clips are 70 s long; the second overlaps the first by 13.5 s (19 %) or 14.5 s (21 %).
        (156.5, True),
        (155.5, False),
    ],
)
def test_overlap_limit(second_start: float, kept: bool) -> None:
    first = scored(100, 170, "90")
    second = scored(second_start, second_start + 70, "80")

    selected = select([first, second], transcript(), DEFAULTS)

    assert spans(selected) == (
        [(100, 170), (second_start, second_start + 70)] if kept else [(100, 170)]
    )


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (10, 80, (10, 80)),
        (10, 120, (10, 100)),  # too long: cut at the last boundary within 90 s
        (10, 50, (10, 70)),  # too short: extended to the first boundary after 60 s
        (10, 70, (10, 70)),
        (10, 100, (10, 100)),
    ],
)
def test_duration_is_clamped_to_the_configured_range(
    start: float, end: float, expected: tuple[float, float]
) -> None:
    assert spans(select([scored(start, end, "90")], transcript(), DEFAULTS)) == [expected]


def test_candidates_snap_to_segment_boundaries() -> None:
    sentences = TranscriptResult(
        segments=[
            Segment(text="a", start_s=0, end_s=12),
            Segment(text="b", start_s=12, end_s=41),
            Segment(text="c", start_s=41, end_s=85),
            Segment(text="d", start_s=85, end_s=100),
        ]
    )

    selected = select([scored(13.4, 83.0, "90")], sentences, DEFAULTS)

    assert spans(selected) == [(12, 85)]


def test_candidate_that_cannot_fit_the_range_is_discarded() -> None:
    long_sentences = TranscriptResult(
        segments=[
            Segment(text="a", start_s=0, end_s=15),
            Segment(text="b", start_s=15, end_s=100),
        ]
    )

    assert select([scored(0, 30, "90")], long_sentences, DEFAULTS) == []


def test_at_most_max_clips_ranked_by_score() -> None:
    candidates = [scored(i * 100, i * 100 + 30, str(76 + i)) for i in range(5)]

    selected = select(candidates, transcript(), DEFAULTS)

    assert [s.total for s in selected] == [Decimal(80), Decimal(79), Decimal(78)]
    assert [s.rank for s in selected] == [1, 2, 3]


def test_non_default_settings_change_the_output() -> None:
    settings = SettingsSchema(min_score=80, clip_min_s=30, clip_max_s=45, max_clips=2)
    candidates = [
        scored(0, 60, "95"),  # cut to 45 s
        scored(100, 120, "90"),  # extended to 30 s
        scored(200, 240, "85"),  # dropped: max_clips=2
        scored(300, 340, "79"),  # dropped: below 80
    ]

    selected = select(candidates, transcript(), settings)

    assert spans(selected) == [(0, 45), (100, 130)]


def test_selection_never_breaks_its_constraints() -> None:
    rng = random.Random(20261020)  # noqa: S311 - reproducible test data
    for _ in range(300):
        duration = rng.uniform(120, 3600)
        text = transcript(duration, step_s=rng.choice([0.5, 2.0, 7.5]))
        clip_min = rng.uniform(5, 40)
        settings = SettingsSchema(
            min_score=rng.uniform(0, 100),
            clip_min_s=clip_min,
            clip_max_s=clip_min + rng.uniform(1, 60),
            max_clips=rng.randint(1, 3),
            max_overlap=rng.uniform(0, 1),
        )
        candidates = []
        for _ in range(rng.randint(0, 25)):
            start = rng.uniform(0, duration - 1)
            end = min(duration, start + rng.uniform(1, 120))
            candidates.append(scored(start, end, f"{rng.uniform(0, 100):.2f}"))

        selected = select(candidates, text, settings)

        assert len(selected) <= settings.max_clips
        for chosen in selected:
            length = chosen.candidate.end_s - chosen.candidate.start_s
            assert settings.clip_min_s - 1e-6 <= length <= settings.clip_max_s + 1e-6
            assert chosen.total >= settings.min_score
        for i, a in enumerate(selected):
            for b in selected[i + 1 :]:
                assert overlap_ratio(a.candidate, b.candidate) <= settings.max_overlap + 1e-9
