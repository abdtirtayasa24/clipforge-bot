import pytest

from clipforge.ai.timestamps import (
    ChunkTranscript,
    merge_chunks,
    parse_offset,
    segments_from_words,
)
from clipforge.ai.types import Segment, TranscriptResult, Word


def words(*items: tuple[str, float, float]) -> list[Word]:
    return [Word(text=text, start_s=start, end_s=end) for text, start, end in items]


def chunk(offset: float, *items: tuple[str, float, float], language: str = "id") -> ChunkTranscript:
    return ChunkTranscript(
        offset,
        TranscriptResult(language=language, segments=segments_from_words(words(*items))),
    )


def timeline(result: TranscriptResult) -> list[tuple[str, float]]:
    return [(word.text, word.start_s) for word in result.words]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.100s", 0.1), ("12s", 12.0), (3.5, 3.5), (2, 2.0), ("abc", None), (None, None)],
)
def test_parse_offset(value: object, expected: float | None) -> None:
    assert parse_offset(value) == expected


def test_overlap_words_are_kept_once() -> None:
    # Chunk 2 starts at 600 s with a 2 s overlap; the cut is at 601 s.
    first = chunk(0, ("halo", 598.0, 598.4), ("semua", 600.2, 600.6), ("apa", 601.3, 601.6))
    second = chunk(600, ("semua", 0.25, 0.6), ("apa", 1.35, 1.6), ("kabar.", 1.8, 2.3))

    merged = merge_chunks([first, second], overlap_s=2, duration_s=900)

    assert [text for text, _ in timeline(merged)] == ["halo", "semua", "apa", "kabar."]
    assert timeline(merged)[2] == ("apa", pytest.approx(601.35))


def test_word_straddling_the_cut_is_not_duplicated() -> None:
    first = chunk(0, ("ini", 600.5, 600.9), ("dia", 600.98, 601.2))
    second = chunk(600, ("dia", 1.02, 1.3), ("datang.", 1.4, 1.9))

    merged = merge_chunks([first, second], overlap_s=2, duration_s=900)

    assert [text for text, _ in timeline(merged)] == ["ini", "dia", "datang."]


def test_timestamps_are_monotonic_and_within_the_media() -> None:
    first = chunk(0, ("a", 5.0, 5.5), ("b", 4.8, 5.2), ("c", 7.0, 6.9 + 0.2))
    second = chunk(10, ("d", 0.5, 2.0), ("e", 9.0, 12.0))

    merged = merge_chunks([first, second], overlap_s=2, duration_s=20)

    starts = [word.start_s for word in merged.words]
    assert starts == sorted(starts)
    assert all(0 <= w.start_s <= w.end_s <= 20 for w in merged.words)
    assert merged.words[-1].end_s == 20


def test_chunks_may_arrive_out_of_order() -> None:
    first = chunk(0, ("satu.", 1, 2))
    second = chunk(600, ("dua.", 5, 6))

    merged = merge_chunks([second, first], overlap_s=2, duration_s=900)

    assert timeline(merged) == [("satu.", 1), ("dua.", 605)]


def test_segments_are_rebuilt_on_sentence_boundaries() -> None:
    first = chunk(
        0,
        ("Halo", 598.0, 598.4),
        ("semua.", 598.5, 599.0),
        ("Apa", 599.6, 599.9),
        ("kabar?", 600.0, 600.4),
    )
    second = chunk(600, ("kabar?", 0.05, 0.4), ("Baik", 1.2, 1.5), ("saja!", 1.6, 2.0))

    merged = merge_chunks([first, second], overlap_s=2, duration_s=900)

    assert [segment.text for segment in merged.segments] == [
        "Halo semua.",
        "Apa kabar?",
        "Baik saja!",
    ]


def test_long_pause_starts_a_new_segment() -> None:
    segments = segments_from_words(words(("satu", 0, 0.5), ("dua", 3.0, 3.5)))

    assert [segment.text for segment in segments] == ["satu", "dua"]


def test_segment_length_is_capped() -> None:
    many = words(*((f"w{i}", i * 1.0, i * 1.0 + 0.9) for i in range(40)))

    segments = segments_from_words(many)

    assert all(s.end_s - s.start_s <= 15 for s in segments)
    assert sum(len(s.words) for s in segments) == 40


def test_segment_level_chunks_are_kept_without_word_timing() -> None:
    coarse = ChunkTranscript(
        600,
        TranscriptResult(
            language="id",
            segments=[
                Segment(text="dari cadangan.", start_s=0.0, end_s=0.8),
                Segment(text="kalimat kedua.", start_s=4.0, end_s=8.0),
            ],
        ),
    )
    detailed = chunk(0, ("awal.", 10, 11))

    merged = merge_chunks([detailed, coarse], overlap_s=2, duration_s=900)

    assert [(s.text, s.start_s, bool(s.words)) for s in merged.segments] == [
        ("awal.", 10, True),
        ("kalimat kedua.", 604.0, False),
    ]


def test_language_is_the_most_common_chunk_language() -> None:
    merged = merge_chunks(
        [chunk(0, language="id"), chunk(600, language="en"), chunk(1200, language="id")],
        overlap_s=2,
        duration_s=1500,
    )

    assert merged.language == "id"
