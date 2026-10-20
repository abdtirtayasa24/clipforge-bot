"""Chunk transcripts to one absolute, monotonic transcript (ADR-012)."""

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from clipforge.ai.types import Segment, TranscriptResult, Word

SENTENCE_END = (".", "?", "!", "\N{HORIZONTAL ELLIPSIS}", "\N{IDEOGRAPHIC FULL STOP}")
CLOSING_QUOTES = "\"')]\N{RIGHT DOUBLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}"
MAX_PAUSE_S = 1.5
MAX_SEGMENT_S = 15.0
# The same word seen at both sides of a cut, timed slightly differently by each chunk.
DUPLICATE_WINDOW_S = 0.5


def parse_offset(value: object) -> float | None:
    """A protobuf Duration as JSON ("1.500s"), or plain seconds."""
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.removesuffix("s"))
        except ValueError:
            return None
    return None


def _ends_sentence(text: str) -> bool:
    return text.rstrip(CLOSING_QUOTES).endswith(SENTENCE_END)


def segments_from_words(words: Sequence[Word]) -> list[Segment]:
    """Groups words into sentence-like segments: punctuation, long pauses or a length cap."""
    segments: list[Segment] = []
    current: list[Word] = []

    def flush() -> None:
        if current:
            segments.append(
                Segment(
                    text=" ".join(word.text for word in current),
                    start_s=current[0].start_s,
                    end_s=current[-1].end_s,
                    words=list(current),
                )
            )
            current.clear()

    for word in words:
        if current and (
            word.start_s - current[-1].end_s > MAX_PAUSE_S
            or word.end_s - current[0].start_s > MAX_SEGMENT_S
        ):
            flush()
        current.append(word)
        if _ends_sentence(word.text):
            flush()
    flush()
    return segments


@dataclass(frozen=True)
class ChunkTranscript:
    """A chunk's transcript with times relative to `offset_s`, the chunk's absolute start."""

    offset_s: float
    transcript: TranscriptResult


@dataclass(frozen=True)
class _Unit:
    """A timed piece of the merged transcript: a word, or a whole segment without word timing."""

    start_s: float
    end_s: float
    text: str
    is_word: bool


def _normalized(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^\w]", "", folded)


def _chunk_units(chunk: ChunkTranscript, keep_from: float, keep_to: float) -> list[_Unit]:
    units: list[_Unit] = []
    for segment in chunk.transcript.segments:
        if segment.words:
            for word in segment.words:
                start = chunk.offset_s + word.start_s
                if keep_from <= start < keep_to and word.text.strip():
                    end = chunk.offset_s + word.end_s
                    units.append(_Unit(start, end, word.text.strip(), is_word=True))
        else:
            start, end = chunk.offset_s + segment.start_s, chunk.offset_s + segment.end_s
            if keep_from <= (start + end) / 2 < keep_to and segment.text.strip():
                units.append(_Unit(start, end, segment.text.strip(), is_word=False))
    return units


def _drop_boundary_duplicate(previous: list[_Unit], following: list[_Unit]) -> list[_Unit]:
    if not previous or not following:
        return following
    last, first = previous[-1], following[0]
    duplicate = (
        last.is_word
        and first.is_word
        and _normalized(last.text) == _normalized(first.text)
        and abs(first.start_s - last.start_s) <= DUPLICATE_WINDOW_S
    )
    return following[1:] if duplicate else following


def merge_chunks(
    chunks: Sequence[ChunkTranscript], *, overlap_s: float, duration_s: float
) -> TranscriptResult:
    """Each overlap is split at its middle, so every moment comes from exactly one chunk."""
    ordered = sorted(chunks, key=lambda chunk: chunk.offset_s)
    cuts = [0.0] + [chunk.offset_s + overlap_s / 2 for chunk in ordered[1:]] + [float("inf")]
    units: list[_Unit] = []
    for index, chunk in enumerate(ordered):
        kept = _chunk_units(chunk, cuts[index], cuts[index + 1])
        units += _drop_boundary_duplicate(units, kept)

    segments: list[Segment] = []
    words: list[Word] = []
    previous_start = 0.0
    for unit in units:
        start = min(max(unit.start_s, previous_start, 0.0), duration_s)
        end = min(max(unit.end_s, start), duration_s)
        previous_start = start
        if unit.is_word:
            words.append(Word(text=unit.text, start_s=start, end_s=end))
            continue
        segments += segments_from_words(words)
        words = []
        segments.append(Segment(text=unit.text, start_s=start, end_s=end))
    segments += segments_from_words(words)

    languages = Counter(c.transcript.language for c in ordered if c.transcript.language)
    language = languages.most_common(1)[0][0] if languages else None
    return TranscriptResult(language=language, segments=segments)
