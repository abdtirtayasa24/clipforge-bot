"""Burned-in ASS subtitles for a 720x1280 clip, timed from the transcript's words (ADR-013)."""

from dataclasses import dataclass

from clipforge.ai.timestamps import ends_sentence
from clipforge.ai.types import TranscriptResult, Word

MAX_LINE_CHARS = 32
MAX_LINES = 2
MAX_PAUSE_S = 1.0

# Bottom-centred, bold white with a black outline; MarginV keeps clear of the
# caption and button overlays that short-video apps draw over the lower part.
HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, \
Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,38,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,\
1,3,1,2,48,48,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass(frozen=True)
class Cue:
    start_s: float
    end_s: float
    lines: list[str]


def format_time(seconds: float) -> str:
    centiseconds = round(seconds * 100)
    hours, rest = divmod(centiseconds, 360_000)
    minutes, rest = divmod(rest, 6_000)
    return f"{hours}:{minutes:02d}:{rest // 100:02d}.{rest % 100:02d}"


def escape(text: str) -> str:
    """Backslashes and braces would otherwise start ASS override codes."""
    text = " ".join(text.split())
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def wrap(texts: list[str]) -> list[str]:
    lines: list[str] = []
    for text in texts:
        if lines and len(lines[-1]) + 1 + len(text) <= MAX_LINE_CHARS:
            lines[-1] = f"{lines[-1]} {text}"
        else:
            lines.append(text)
    return lines


def clip_words(transcript: TranscriptResult, start_s: float, end_s: float) -> list[Word]:
    """Words overlapping the clip, re-based to its start; segments without word timing
    (the fallback transcriber) have their duration shared evenly between their words."""
    duration_s = end_s - start_s
    words: list[Word] = []
    for segment in transcript.segments:
        timed = segment.words
        if not timed:
            texts = segment.text.split()
            step = (segment.end_s - segment.start_s) / max(len(texts), 1)
            timed = [
                Word(
                    text=text,
                    start_s=segment.start_s + step * index,
                    end_s=segment.start_s + step * (index + 1),
                )
                for index, text in enumerate(texts)
            ]
        for word in timed:
            if word.end_s > start_s and word.start_s < end_s and word.text.strip():
                words.append(
                    Word(
                        text=word.text.strip(),
                        start_s=min(max(word.start_s - start_s, 0.0), duration_s),
                        end_s=min(max(word.end_s - start_s, 0.0), duration_s),
                    )
                )
    return words


def cues(words: list[Word]) -> list[Cue]:
    """Up to two lines per cue; a sentence end or a pause also starts a new cue."""
    result: list[Cue] = []
    current: list[Word] = []

    def flush() -> None:
        if current:
            result.append(
                Cue(current[0].start_s, current[-1].end_s, wrap([w.text for w in current]))
            )
            current.clear()

    for word in words:
        if current and (
            word.start_s - current[-1].end_s > MAX_PAUSE_S
            or len(wrap([*(w.text for w in current), word.text])) > MAX_LINES
        ):
            flush()
        current.append(word)
        if ends_sentence(word.text):
            flush()
    flush()
    return result


def build_ass(transcript: TranscriptResult, start_s: float, end_s: float) -> str:
    events = [
        f"Dialogue: 0,{format_time(cue.start_s)},{format_time(cue.end_s)},Default,,0,0,0,,"
        + "\\N".join(escape(line) for line in cue.lines)
        for cue in cues(clip_words(transcript, start_s, end_s))
    ]
    return HEADER + "".join(f"{event}\n" for event in events)
