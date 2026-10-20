"""Versioned analysis prompts and the parsing shared by every analysis adapter (ADR-011, 025)."""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from clipforge.ai.types import AnalysisContext, ClipCandidate, DimensionScores, Segment
from clipforge.queue.errors import TransientError

logger = logging.getLogger(__name__)

PROMPT_VERSION = "analysis-v1"
TITLE_MAX_CHARS = 70
DIMENSIONS = ("hook", "emotion", "clarity", "relevance", "visual", "completeness")

SYSTEM_PROMPT = """You find short-form clip candidates in long videos for an editor.
Score each candidate from 0 to 100 on six dimensions:
- hook: how strongly the first seconds grab attention
- emotion: emotional impact (humor, surprise, tension, inspiration)
- clarity: makes sense on its own without the rest of the video
- relevance: interest for a broad Indonesian short-video audience
- visual: how visually engaging the moment is likely to be
- completeness: has a clear beginning and a satisfying end
Do not compute a total score. Only use times that appear in the transcript.
Write `title` (at most 70 characters) and `hook_line` in the transcript's language. They must
only state what is said in the clip's transcript: never invent facts, numbers, names or claims.
Answer with JSON only."""


def _clock(seconds: float) -> str:
    return f"{seconds:.1f}"


def transcript_lines(segments: Sequence[Segment]) -> str:
    return "\n".join(f"[{_clock(s.start_s)}-{_clock(s.end_s)}] {s.text}" for s in segments)


def _context_lines(context: AnalysisContext) -> str:
    lines = [f"Video title: {context.video_title}"]
    if context.channel_title:
        lines.append(f"Channel: {context.channel_title}")
    lines.append(f"Transcript language: {context.language or 'unknown (detect it)'}")
    return "\n".join(lines)


def window_prompt(segments: Sequence[Segment], context: AnalysisContext) -> str:
    return f"""{_context_lines(context)}
Transcript excerpt (times are seconds from the start of the video):
{transcript_lines(segments)}

Propose up to 5 standalone clips of {context.clip_min_s:g}-{context.clip_max_s:g} seconds from
this excerpt that could perform well as vertical short videos. Start and end on sentence
boundaries. Return {{"candidates": [{{"start_s", "end_s", "hook", "emotion", "clarity",
"relevance", "visual", "completeness", "title", "hook_line", "topic", "keywords",
"explanation"}}]}}. Return {{"candidates": []}} when nothing qualifies."""


def rerank_prompt(
    candidates: Sequence[ClipCandidate],
    excerpts: Sequence[Sequence[Segment]],
    context: AnalysisContext,
) -> str:
    blocks = []
    for index, (candidate, segments) in enumerate(zip(candidates, excerpts, strict=True)):
        blocks.append(
            f"Candidate {index} [{_clock(candidate.start_s)}-{_clock(candidate.end_s)}] "
            f'current title: "{candidate.title}"\n{transcript_lines(segments)}'
        )
    joined = "\n\n".join(blocks)
    return f"""{_context_lines(context)}
These are the shortlisted clip candidates. The images that follow are frames sampled from the
video; each image is preceded by its time in seconds. Re-score every candidate on all six
dimensions, judging `visual` from the frames inside its time range. Improve the title and
hook line when useful, following the same rules.

{joined}

Return {{"candidates": [{{"index", "hook", "emotion", "clarity", "relevance", "visual",
"completeness", "title", "hook_line", "explanation"}}]}} with one entry per candidate."""


def repair_prompt(error: str) -> str:
    return (
        "Your previous answer was not valid for the requested JSON format: "
        f"{error[:500]}\nReturn the corrected JSON only."
    )


def truncate_title(title: str, limit: int = TITLE_MAX_CHARS) -> str:
    """Cuts at a word boundary when the model ignored the length limit."""
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    cut = title[: limit + 1].rsplit(" ", 1)[0] if " " in title[:limit] else title[:limit]
    return cut[:limit].rstrip(" ,;:-")


def _score(value: Any) -> int:
    return max(0, min(100, round(float(value))))


class _Scores(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hook: int
    emotion: int
    clarity: int
    relevance: int
    visual: int
    completeness: int

    @field_validator(*DIMENSIONS, mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> int:
        return _score(value)

    def dimensions(self) -> DimensionScores:
        return DimensionScores(**{name: getattr(self, name) for name in DIMENSIONS})


class _Proposal(_Scores):
    start_s: float
    end_s: float
    title: str = Field(min_length=1)
    hook_line: str = Field(min_length=1)
    topic: str = ""
    keywords: list[str] = Field(default_factory=list)
    explanation: str = ""

    @field_validator("title", "hook_line")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class _Proposals(BaseModel):
    candidates: list[_Proposal]


class _Rescore(_Scores):
    index: int
    title: str | None = None
    hook_line: str | None = None
    explanation: str | None = None


class _Rescores(BaseModel):
    candidates: list[_Rescore]


def _json_text(text: str) -> str:
    """Free models may wrap JSON in a Markdown fence despite the instructions."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return fenced.group(1) if fenced else text


def parse_proposals(text: str) -> list[ClipCandidate]:
    parsed = _Proposals.model_validate_json(_json_text(text))
    return [
        ClipCandidate(
            start_s=max(0.0, p.start_s),
            end_s=max(0.0, p.end_s),
            scores=p.dimensions(),
            title=truncate_title(p.title),
            hook_line=p.hook_line,
            topic=p.topic,
            keywords=p.keywords,
            explanation=p.explanation,
        )
        for p in parsed.candidates
        if p.end_s > p.start_s
    ]


def parse_rescores(text: str, candidates: Sequence[ClipCandidate]) -> list[ClipCandidate]:
    """Applies the re-rank scores by index; candidates the model skipped keep their scores."""
    parsed = _Rescores.model_validate_json(_json_text(text))
    updated = list(candidates)
    for item in parsed.candidates:
        if not 0 <= item.index < len(updated):
            continue
        current = updated[item.index]
        updated[item.index] = current.model_copy(
            update={
                "scores": item.dimensions(),
                "title": truncate_title(item.title)
                if item.title and item.title.strip()
                else current.title,
                "hook_line": (item.hook_line or "").strip() or current.hook_line,
                "explanation": item.explanation or current.explanation,
            }
        )
    return updated


class Repair(BaseModel):
    previous_answer: str
    error: str


Ask = Callable[[Repair | None], Awaitable[str]]


async def with_repair[T](ask: Ask, parse: Callable[[str], T]) -> T:
    """Asks once, and once more with the validation error; then gives up (ADR-011)."""
    text = await ask(None)
    try:
        return parse(text)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            "model output invalid; asking for a repair", extra={"reason": str(exc)[:300]}
        )
        repair = Repair(previous_answer=text, error=str(exc))
    text = await ask(repair)
    try:
        return parse(text)
    except (ValidationError, ValueError):
        raise TransientError("the model returned invalid JSON twice") from None


PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "candidates": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start_s": {"type": "NUMBER"},
                    "end_s": {"type": "NUMBER"},
                    **{name: {"type": "INTEGER"} for name in DIMENSIONS},
                    "title": {"type": "STRING"},
                    "hook_line": {"type": "STRING"},
                    "topic": {"type": "STRING"},
                    "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "explanation": {"type": "STRING"},
                },
                "required": ["start_s", "end_s", *DIMENSIONS, "title", "hook_line"],
            },
        }
    },
    "required": ["candidates"],
}

RESCORE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "candidates": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    **{name: {"type": "INTEGER"} for name in DIMENSIONS},
                    "title": {"type": "STRING"},
                    "hook_line": {"type": "STRING"},
                    "explanation": {"type": "STRING"},
                },
                "required": ["index", *DIMENSIONS],
            },
        }
    },
    "required": ["candidates"],
}


def schema_hint(schema: dict[str, Any]) -> str:
    """The JSON schema spelled out for providers without structured output."""
    return "JSON schema of the answer:\n" + json.dumps(schema, separators=(",", ":"))
