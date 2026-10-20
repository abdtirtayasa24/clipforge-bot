"""Server-side viral-potential score (spec "Viral-potential scoring", ADR-010)."""

from dataclasses import dataclass
from decimal import Decimal

from clipforge.ai.types import ClipCandidate, DimensionScores

# Fixed by the spec; deliberately not configurable (ADR-026).
WEIGHTS = {
    "hook": Decimal("0.25"),
    "emotion": Decimal("0.20"),
    "clarity": Decimal("0.20"),
    "relevance": Decimal("0.15"),
    "visual": Decimal("0.10"),
    "completeness": Decimal("0.10"),
}
SCORE_QUANTUM = Decimal("0.01")


def score(scores: DimensionScores) -> Decimal:
    """`0.25H + 0.20E + 0.20C + 0.15R + 0.10V + 0.10F`; a ranking heuristic, not a probability."""
    total = sum(
        (Decimal(getattr(scores, name)) * weight for name, weight in WEIGHTS.items()), Decimal(0)
    )
    return total.quantize(SCORE_QUANTUM).normalize()


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: ClipCandidate
    total: Decimal
    rank: int | None = None

    @classmethod
    def of(cls, candidate: ClipCandidate) -> "ScoredCandidate":
        return cls(candidate=candidate, total=score(candidate.scores))
