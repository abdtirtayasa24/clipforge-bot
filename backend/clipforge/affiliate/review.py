"""Administrator decisions on clip affiliate links; every change is an `affiliate_events` row."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clipforge.affiliate.caption import ExportLink
from clipforge.affiliate.matcher import remove_links
from clipforge.db.models import AffiliateEvent, AffiliateProduct, ClipAffiliateLink

Decision = Literal["approved", "rejected"]
MANUAL_REASON = "Selected by the administrator"


class LinkNotFound(Exception):
    pass


class ProductUnavailable(Exception):
    pass


class AlreadyLinked(Exception):
    pass


def _event(
    link: ClipAffiliateLink, event_type: str, admin_id: uuid.UUID, **extra: object
) -> AffiliateEvent:
    return AffiliateEvent(
        clip_id=link.clip_id,
        affiliate_link_id=link.id,
        event_type=event_type,
        details={
            "link_id": str(link.id),
            "product_id": str(link.product_id),
            "admin_user_id": str(admin_id),
            **extra,
        },
    )


async def _locked_link(
    session: AsyncSession, clip_id: uuid.UUID, link_id: uuid.UUID
) -> ClipAffiliateLink:
    link = await session.scalar(
        select(ClipAffiliateLink)
        .where(ClipAffiliateLink.id == link_id, ClipAffiliateLink.clip_id == clip_id)
        .with_for_update()
    )
    if link is None:
        raise LinkNotFound
    return link


async def _approve(session: AsyncSession, link: ClipAffiliateLink, primary: bool | None) -> None:
    """Only one approved primary per clip; the first approval becomes primary by default."""
    others = (
        (ClipAffiliateLink.clip_id == link.clip_id)
        & (ClipAffiliateLink.id != link.id)
        & (ClipAffiliateLink.status == "approved")
        & ClipAffiliateLink.is_primary
    )
    if primary is None:
        primary = await session.scalar(select(ClipAffiliateLink.id).where(others)) is None
    if primary:
        await session.execute(update(ClipAffiliateLink).where(others).values(is_primary=False))
    link.status = "approved"
    link.is_primary = primary
    link.reviewed_at = datetime.now(UTC)


async def review_link(
    session: AsyncSession,
    clip_id: uuid.UUID,
    link_id: uuid.UUID,
    admin_id: uuid.UUID,
    decision: Decision,
    primary: bool | None,
) -> None:
    link = await _locked_link(session, clip_id, link_id)
    if decision == "approved":
        await _approve(session, link, primary)
    else:
        link.status, link.is_primary, link.reviewed_at = "rejected", False, datetime.now(UTC)
    await session.flush()
    session.add(_event(link, decision, admin_id, is_primary=link.is_primary))
    await session.commit()


async def add_manual_link(
    session: AsyncSession,
    clip_id: uuid.UUID,
    product_id: uuid.UUID,
    admin_id: uuid.UUID,
    primary: bool | None,
) -> None:
    """An administrator-chosen catalog product, approved directly."""
    product = await session.get(AffiliateProduct, product_id)
    if product is None or not product.active:
        raise ProductUnavailable
    existing = await session.scalar(
        select(ClipAffiliateLink.id).where(
            ClipAffiliateLink.clip_id == clip_id, ClipAffiliateLink.product_id == product_id
        )
    )
    if existing is not None:
        raise AlreadyLinked
    link = ClipAffiliateLink(
        clip_id=clip_id,
        product_id=product_id,
        relevance_score=Decimal(1),
        match_reason=MANUAL_REASON,
    )
    session.add(link)
    await session.flush()
    await _approve(session, link, primary)
    await session.flush()
    session.add(_event(link, "manual_selected", admin_id, is_primary=link.is_primary))
    await session.commit()


async def remove_link(
    session: AsyncSession, clip_id: uuid.UUID, link_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    link = await _locked_link(session, clip_id, link_id)
    event = _event(link, "removed", admin_id, status=link.status)
    await remove_links(session, ClipAffiliateLink.id == link_id)
    event.affiliate_link_id = None
    session.add(event)
    await session.commit()


async def approved_export_links(session: AsyncSession, clip_id: uuid.UUID) -> list[ExportLink]:
    rows = await session.execute(
        select(AffiliateProduct, ClipAffiliateLink.is_primary)
        .join(ClipAffiliateLink, ClipAffiliateLink.product_id == AffiliateProduct.id)
        .where(ClipAffiliateLink.clip_id == clip_id, ClipAffiliateLink.status == "approved")
        .order_by(ClipAffiliateLink.relevance_score.desc(), ClipAffiliateLink.id)
    )
    return [
        ExportLink(product.name, product.merchant, product.affiliate_url, is_primary)
        for product, is_primary in rows
    ]
