"""Provider-independent AI results; all times are seconds (ADR-009)."""

from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, Field, model_validator


class TimeSpan(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_s < self.start_s:
            raise ValueError("end_s must not be before start_s")
        return self


class Word(TimeSpan):
    text: str


class Segment(TimeSpan):
    text: str
    words: list[Word] = Field(default_factory=list)


class TranscriptResult(BaseModel):
    language: str | None = None
    segments: list[Segment] = Field(default_factory=list)

    @property
    def words(self) -> list[Word]:
        return [word for segment in self.segments for word in segment.words]

    @property
    def text(self) -> str:
        return " ".join(segment.text for segment in self.segments)


Score = Annotated[int, Field(ge=0, le=100)]


class DimensionScores(BaseModel):
    """The six spec dimensions (0 to 100); the total is computed server-side (ADR-010)."""

    hook: Score
    emotion: Score
    clarity: Score
    relevance: Score
    visual: Score
    completeness: Score


class ClipCandidate(TimeSpan):
    scores: DimensionScores
    title: str
    hook_line: str
    topic: str = ""
    keywords: list[str] = Field(default_factory=list)
    explanation: str = ""


class AnalysisContext(BaseModel):
    video_title: str
    channel_title: str | None = None
    language: str | None = None
    duration_s: float
    clip_min_s: float = 60
    clip_max_s: float = 90


class Frame(BaseModel):
    time_s: float = Field(ge=0)
    path: Path
