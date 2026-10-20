"""Affiliate product matching (ADR-020): catalog products only, never invented links.

Products are filtered by language/region and tag/category overlap with the clip, then ranked
by embedding similarity, or by the share of product tags found in the clip when embeddings are
unavailable. Scores are ranking heuristics, not conversion predictions.
"""

import hashlib
import logging
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import ColumnElement, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.ai.protocols import EmbeddingProvider
from clipforge.ai.usage import UsageRecorder
from clipforge.db.models import (
    AffiliateEvent,
    AffiliateProduct,
    AffiliateProductEmbedding,
    Clip,
    ClipAffiliateLink,
    ClipCandidate,
)
from clipforge.settings_schema import load_settings

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3
MIN_NAME_TERM_CHARS = 3
QUERY_TRANSCRIPT_CHARS = 2000
_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class ClipText:
    title: str
    topic: str
    keywords: Sequence[str]
    transcript: str
    language: str | None


@dataclass(frozen=True)
class Product:
    id: uuid.UUID
    name: str
    merchant: str
    category: str | None
    tags: Sequence[str]
    language_code: str | None
    region_code: str | None


@dataclass(frozen=True)
class Suggestion:
    product_id: uuid.UUID
    score: Decimal
    reason: str
    is_primary: bool


@dataclass(frozen=True)
class EmbeddingSetup:
    provider: EmbeddingProvider
    model: str
    record: UsageRecorder


def _normalized(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def _language(code: str | None) -> str | None:
    return code.split("-")[0].lower() if code else None


def product_terms(product: Product) -> list[str]:
    terms = [*product.tags, *([product.category] if product.category else [])]
    if not terms:
        terms = [w for w in _WORD.findall(product.name) if len(w) >= MIN_NAME_TERM_CHARS]
    return list(dict.fromkeys(term for term in map(_normalized, terms) if term))


def shared_terms(product: Product, clip: ClipText) -> list[str]:
    haystack = (
        f" {_normalized(' '.join([clip.title, clip.topic, *clip.keywords, clip.transcript]))} "
    )
    return [term for term in product_terms(product) if f" {term} " in haystack]


def eligible(product: Product, clip: ClipText, region: str | None) -> bool:
    clip_language, product_language = _language(clip.language), _language(product.language_code)
    if product_language and clip_language and product_language != clip_language:
        return False
    if product.region_code and region and product.region_code != region:
        return False
    return bool(shared_terms(product, clip))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


def rank(
    products: Sequence[Product],
    clip: ClipText,
    *,
    region: str | None,
    threshold: float,
    similarities: Mapping[uuid.UUID, float] | None = None,
) -> list[Suggestion]:
    """At most three products at or above `threshold`; the best is the primary candidate."""
    scored: list[tuple[float, Product, str]] = []
    for product in products:
        if not eligible(product, clip, region):
            continue
        shared = shared_terms(product, clip)
        terms = ", ".join(shared)
        if similarities is not None and product.id in similarities:
            score = min(max(similarities[product.id], 0.0), 1.0)
            reason = f"Related topic (semantic similarity {score:.2f}); shared terms: {terms}"
        else:
            score = len(shared) / len(product_terms(product))
            reason = f"Related by keywords: {terms}"
        scored.append((round(score, 3), product, reason))
    scored.sort(key=lambda item: (-item[0], item[1].name, str(item[1].id)))
    picked = [item for item in scored if item[0] >= threshold][:MAX_SUGGESTIONS]
    return [
        Suggestion(product.id, Decimal(str(score)), reason, is_primary=index == 0)
        for index, (score, product, reason) in enumerate(picked)
    ]


def clip_query(clip: ClipText) -> str:
    text = " ".join(
        part for part in (clip.title, clip.topic, ", ".join(clip.keywords), clip.transcript) if part
    )
    return f"task: search result | query: {text[:QUERY_TRANSCRIPT_CHARS]}"


def product_document(product: Product) -> str:
    details = "; ".join(
        part for part in (product.merchant, product.category or "", ", ".join(product.tags)) if part
    )
    return f"title: {product.name} | text: {details}"


async def remove_links(session: AsyncSession, condition: ColumnElement[bool]) -> int:
    """Deletes links; their events stay, unlinked, with the ids kept in `details`."""
    link_ids = select(ClipAffiliateLink.id).where(condition).scalar_subquery()
    await session.execute(
        update(AffiliateEvent)
        .where(AffiliateEvent.affiliate_link_id.in_(link_ids))
        .values(affiliate_link_id=None)
    )
    result = await session.execute(delete(ClipAffiliateLink).where(condition))
    return int(result.rowcount)  # type: ignore[attr-defined]


def _product(row: AffiliateProduct) -> Product:
    return Product(
        row.id, row.name, row.merchant, row.category, row.tags, row.language_code, row.region_code
    )


class AffiliateMatcher:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], embedding: EmbeddingSetup | None
    ) -> None:
        self._sessions = sessions
        self._embedding = embedding

    async def has_links(self, clip_id: uuid.UUID) -> bool:
        async with self._sessions() as session:
            found = await session.scalar(
                select(ClipAffiliateLink.id).where(ClipAffiliateLink.clip_id == clip_id).limit(1)
            )
        return found is not None

    async def match_clip(self, clip_id: uuid.UUID, *, replace_suggested: bool = False) -> int:
        """Stores new suggestions for the clip and returns how many were added.

        Products already approved or rejected for the clip are never suggested again.
        """
        async with self._sessions() as session:
            settings = await load_settings(session)
            row = (
                await session.execute(
                    select(Clip, ClipCandidate)
                    .join(ClipCandidate, ClipCandidate.id == Clip.candidate_id)
                    .where(Clip.id == clip_id)
                )
            ).one()
            clip_row, candidate = row
            reviewed = set(
                await session.scalars(
                    select(ClipAffiliateLink.product_id).where(
                        ClipAffiliateLink.clip_id == clip_id,
                        ClipAffiliateLink.status != "suggested",
                    )
                )
            )
            products = [
                _product(product)
                for product in await session.scalars(
                    select(AffiliateProduct)
                    .where(AffiliateProduct.active)
                    .order_by(AffiliateProduct.created_at)
                )
                if product.id not in reviewed
            ]
        clip = ClipText(
            title=clip_row.title_override or clip_row.title or "",
            topic=candidate.topic or "",
            keywords=candidate.keywords,
            transcript=candidate.transcript_excerpt or "",
            language=candidate.language,
        )
        region = settings.discovery.region_code
        candidates = [product for product in products if eligible(product, clip, region)]
        similarities = await self._similarities(clip, candidates) if candidates else None
        suggestions = rank(
            candidates,
            clip,
            region=region,
            threshold=settings.affiliate_min_relevance,
            similarities=similarities,
        )
        method = "embedding" if similarities is not None else "keyword"
        async with self._sessions.begin() as session:
            if replace_suggested:
                await remove_links(
                    session,
                    (ClipAffiliateLink.clip_id == clip_id)
                    & (ClipAffiliateLink.status == "suggested"),
                )
            added = 0
            for suggestion in suggestions:
                link_id = await session.scalar(
                    insert(ClipAffiliateLink)
                    .values(
                        clip_id=clip_id,
                        product_id=suggestion.product_id,
                        relevance_score=suggestion.score,
                        match_reason=suggestion.reason,
                        is_primary=suggestion.is_primary,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[ClipAffiliateLink.clip_id, ClipAffiliateLink.product_id]
                    )
                    .returning(ClipAffiliateLink.id)
                )
                if link_id is None:
                    continue
                added += 1
                session.add(
                    AffiliateEvent(
                        clip_id=clip_id,
                        affiliate_link_id=link_id,
                        event_type="suggested",
                        details={
                            "link_id": str(link_id),
                            "product_id": str(suggestion.product_id),
                            "relevance_score": str(suggestion.score),
                            "method": method,
                        },
                    )
                )
            session.add(
                AffiliateEvent(
                    clip_id=clip_id,
                    event_type="matched",
                    details={"method": method, "suggestions": added, "rematch": replace_suggested},
                )
            )
        return added

    async def _similarities(
        self, clip: ClipText, products: Sequence[Product]
    ) -> dict[uuid.UUID, float] | None:
        """Cosine similarity per product, or None when embeddings are unavailable."""
        if self._embedding is None:
            return None
        setup = self._embedding
        documents = {product.id: product_document(product) for product in products}
        hashes = {
            product_id: hashlib.sha256(f"{setup.model}\n{text}".encode()).hexdigest()
            for product_id, text in documents.items()
        }
        try:
            async with self._sessions() as session:
                cached = {
                    row.product_id: row.embedding
                    for row in await session.scalars(
                        select(AffiliateProductEmbedding).where(
                            AffiliateProductEmbedding.product_id.in_(documents)
                        )
                    )
                    if row.content_sha256 == hashes[row.product_id]
                }
            stale = [product_id for product_id in documents if product_id not in cached]
            vectors = await setup.provider.embed(
                [clip_query(clip), *(documents[product_id] for product_id in stale)],
                record=setup.record,
            )
        except Exception as exc:
            logger.warning(
                "affiliate embeddings unavailable; using keyword matching",
                extra={"reason": type(exc).__name__},
            )
            return None
        fresh = dict(zip(stale, vectors[1:], strict=True))
        if fresh:
            async with self._sessions.begin() as session:
                for product_id, vector in fresh.items():
                    values = {
                        "model": setup.model,
                        "content_sha256": hashes[product_id],
                        "embedding": vector,
                    }
                    await session.execute(
                        insert(AffiliateProductEmbedding)
                        .values(product_id=product_id, **values)
                        .on_conflict_do_update(
                            index_elements=[AffiliateProductEmbedding.product_id], set_=values
                        )
                    )
        query = vectors[0]
        return {
            product_id: cosine(query, {**cached, **fresh}[product_id]) for product_id in documents
        }
