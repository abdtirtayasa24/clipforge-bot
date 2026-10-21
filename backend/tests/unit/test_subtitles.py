from pathlib import Path

import pytest

from clipforge.ai.types import Segment, TranscriptResult, Word
from clipforge.media.subtitles import (
    MAX_LINE_CHARS,
    MAX_LINES,
    MAX_TITLE_LINE_CHARS,
    MAX_TITLE_LINES,
    build_ass,
    format_time,
)

GOLDEN = Path(__file__).parent.parent / "fixtures" / "subtitles"


def spoken(*items: tuple[str, float, float]) -> Segment:
    words = [Word(text=text, start_s=start, end_s=end) for text, start, end in items]
    return Segment(
        text=" ".join(word.text for word in words),
        start_s=words[0].start_s,
        end_s=words[-1].end_s,
        words=words,
    )


def golden(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8")


def dialogues(ass: str) -> list[list[str]]:
    return [
        line.removeprefix("Dialogue: ").split(",", 9)
        for line in ass.splitlines()
        if line.startswith("Dialogue: ")
    ]


def test_times_are_rebased_to_the_clip_start() -> None:
    transcript = TranscriptResult(
        segments=[
            spoken(("Sebelumnya", 98.0, 99.5)),
            spoken(
                ("Halo", 100.2, 100.5),
                ("semua,", 100.5, 100.9),
                ("apa", 101.0, 101.2),
                ("kabar?", 101.2, 101.7),
            ),
            spoken(
                ("Hari", 103.5, 103.8),
                ("ini", 103.8, 104.0),
                ("kita", 104.0, 104.3),
                ("bahas", 104.3, 104.7),
                ("AI.", 104.7, 105.2),
            ),
            spoken(("Setelah", 130.5, 131.0)),
        ]
    )

    assert build_ass(transcript, 100.0, 130.0) == golden("rebase.ass")


def test_long_text_wraps_to_two_lines_per_cue() -> None:
    text = (
        "ini adalah kalimat yang sangat panjang sekali untuk menguji "
        "pembungkusan baris subtitle agar tetap rapi dan terbaca"
    )
    items = [
        (word, 10.0 + 0.3 * index, 10.0 + 0.3 * (index + 1))
        for index, word in enumerate(text.split())
    ]

    assert build_ass(TranscriptResult(segments=[spoken(*items)]), 10.0, 40.0) == golden(
        "wrapping.ass"
    )


def test_ass_special_characters_are_escaped() -> None:
    transcript = TranscriptResult(
        segments=[
            spoken(
                ("Kode", 1.0, 1.4),
                ("{\\an8}", 1.4, 1.8),
                ("dan", 1.8, 2.0),
                ("C:\\path", 2.0, 2.5),
            )
        ]
    )

    assert build_ass(transcript, 0.0, 10.0) == golden("escaping.ass")


def test_segments_without_word_timing_are_spread_evenly() -> None:
    transcript = TranscriptResult(
        segments=[
            Segment(text="Satu dua tiga empat.", start_s=50.0, end_s=52.0),
            Segment(text="Lima enam.", start_s=53.0, end_s=54.0),
        ]
    )

    cues = dialogues(build_ass(transcript, 51.0, 80.0))

    assert [(cue[1], cue[2], cue[9]) for cue in cues] == [
        ("0:00:00.00", "0:00:01.00", "tiga empat."),
        ("0:00:02.00", "0:00:03.00", "Lima enam."),
    ]


def test_cues_stay_inside_the_clip_and_the_line_limits() -> None:
    words = [(f"kata{index}", 4.0 + 0.25 * index, 4.0 + 0.25 * index + 0.2) for index in range(200)]
    transcript = TranscriptResult(segments=[spoken(*words)])

    cues = dialogues(build_ass(transcript, 5.0, 35.0))

    assert cues
    previous_end = "0:00:00.00"
    for cue in cues:
        lines = cue[9].split("\\N")
        assert len(lines) <= MAX_LINES
        assert all(len(line) <= MAX_LINE_CHARS for line in lines)
        assert previous_end <= cue[1] <= cue[2] <= "0:00:30.00"
        previous_end = cue[2]


def test_title_shows_at_the_top_over_the_opening_seconds() -> None:
    transcript = TranscriptResult(segments=[spoken(("Halo", 10.5, 11.0))])

    events = dialogues(build_ass(transcript, 10.0, 40.0, "Cara  hemat {listrik}\\ di rumah"))

    assert events[0][:4] == ["1", "0:00:00.00", "0:00:03.00", "Title"]
    assert events[0][9] == "Cara hemat \\{listrik\\}\\\\\\Ndi rumah"
    assert [event[3] for event in events[1:]] == ["Default"]
    assert "Style: Title," in build_ass(transcript, 10.0, 40.0)


def test_title_is_wrapped_and_truncated_to_its_line_limits() -> None:
    title = " ".join(["kata"] * 30)

    events = dialogues(build_ass(TranscriptResult(segments=[]), 0.0, 2.0, title))

    lines = events[0][9].split("\\N")
    assert events[0][2] == "0:00:02.00"
    assert len(lines) == MAX_TITLE_LINES
    assert all(len(line) <= MAX_TITLE_LINE_CHARS for line in lines[:-1])
    assert lines[-1].endswith("…")


@pytest.mark.parametrize("title", [None, "", "   "])
def test_no_title_event_without_a_title(title: str | None) -> None:
    transcript = TranscriptResult(segments=[spoken(("Halo", 10.5, 11.0))])

    events = dialogues(build_ass(transcript, 10.0, 40.0, title))

    assert [event[3] for event in events] == ["Default"]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.0, "0:00:00.00"), (1.005, "0:00:01.00"), (59.999, "0:01:00.00"), (3725.456, "1:02:05.46")],
)
def test_format_time(seconds: float, expected: str) -> None:
    assert format_time(seconds) == expected
