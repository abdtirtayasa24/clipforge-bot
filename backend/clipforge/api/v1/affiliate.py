import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Self

import httpx
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import case, func, select

from clipforge.affiliate.matcher import AffiliateMatcher, EmbeddingSetup
from clipforge.affiliate.review import (
    AlreadyLinked,
    Decision,
    LinkNotFound,
    ProductUnavailable,
    add_manual_link,
    remove_link,
    review_link,
)
from clipforge.affiliate.validation import LinkRejected, check_affiliate_url, destination_host
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import ProviderConfigError, resolve_providers
from clipforge.ai.usage import usage_recorder
from clipforge.api.deps import DbSession, SessionsDep
from clipforge.api.errors import Conflict, NotFound, Unprocessable
from clipforge.auth.deps import CurrentAdmin, require_admin
from clipforge.db.models import AffiliateProduct, Clip, ClipAffiliateLink
from clipforge.discovery.regions import ISO_3166_ALPHA2
from clipforge.pipeline.registry import provider_registry
from clipforge.pipeline.stages.affiliate import affiliate_links_enabled
from clipforge.settings_schema import load_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/affiliate", tags=["affiliate"], dependencies=[Depends(require_admin)])
clip_links_router = APIRouter(
    prefix="/clips/{clip_id}/affiliate-links",
    tags=["affiliate"],
    dependencies=[Depends(require_admin)],
)

Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Tag = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=50)
]
LanguageCode = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]
REQUIRED_FIELDS = ("name", "merchant", "affiliate_url", "destination_domain", "tags", "active")


def _iso_region(value: str | None) -> str | None:
    if value is not None and value not in ISO_3166_ALPHA2:
        raise ValueError("region_code must be an ISO 3166-1 alpha-2 code")
    return value


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Text200
    merchant: Text200
    affiliate_url: str = Field(min_length=1, max_length=2048)
    destination_domain: str = Field(min_length=1, max_length=253)
    category: Text200 | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    language_code: LanguageCode | None = None
    region_code: str | None = None
    active: bool = True

    _region = field_validator("region_code")(_iso_region)


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Text200 | None = None
    merchant: Text200 | None = None
    affiliate_url: str | None = Field(default=None, min_length=1, max_length=2048)
    destination_domain: str | None = Field(default=None, min_length=1, max_length=253)
    category: Text200 | None = None
    tags: list[Tag] | None = Field(default=None, max_length=20)
    language_code: LanguageCode | None = None
    region_code: str | None = None
    active: bool | None = None

    _region = field_validator("region_code")(_iso_region)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        for field in REQUIRED_FIELDS:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    merchant: str
    affiliate_url: str
    destination_domain: str
    category: str | None
    tags: list[str]
    language_code: str | None
    region_code: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int


async def _validated_link(session: DbSession, values: dict[str, Any]) -> dict[str, Any]:
    """Normalizes the domain and checks the URL against it and the merchant allowlist."""
    merchant_domains = (await load_settings(session)).affiliate_merchant_domains
    try:
        domain = destination_host(values["destination_domain"])
        url = check_affiliate_url(values["affiliate_url"], domain, merchant_domains)
    except LinkRejected as exc:
        raise Unprocessable(str(exc)) from None
    return {**values, "destination_domain": domain, "affiliate_url": url}


async def _get_product(session: DbSession, product_id: uuid.UUID) -> AffiliateProduct:
    product = await session.get(AffiliateProduct, product_id)
    if product is None:
        raise NotFound("affiliate product not found")
    return product


@router.get("/products")
async def list_products(
    session: DbSession,
    active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductPage:
    query = select(AffiliateProduct)
    if active is not None:
        query = query.where(AffiliateProduct.active == active)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    products = await session.scalars(
        query.order_by(AffiliateProduct.created_at.desc(), AffiliateProduct.id)
        .limit(limit)
        .offset(offset)
    )
    return ProductPage(
        items=[ProductOut.model_validate(product) for product in products],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.post("/products", status_code=status.HTTP_201_CREATED)
async def create_product(body: ProductCreate, session: DbSession) -> ProductOut:
    product = AffiliateProduct(**await _validated_link(session, body.model_dump()))
    session.add(product)
    await session.commit()
    await session.refresh(product)
    logger.info(
        "affiliate product created",
        extra={"product_id": str(product.id), "domain": product.destination_domain},
    )
    return ProductOut.model_validate(product)


@router.patch("/products/{product_id}")
async def update_product(
    product_id: uuid.UUID, body: ProductUpdate, session: DbSession
) -> ProductOut:
    product = await _get_product(session, product_id)
    changes = body.model_dump(exclude_unset=True)
    if {"affiliate_url", "destination_domain"} & changes.keys():
        link = {
            "affiliate_url": changes.get("affiliate_url", product.affiliate_url),
            "destination_domain": changes.get("destination_domain", product.destination_domain),
        }
        changes |= await _validated_link(session, link)
    for field, value in changes.items():
        setattr(product, field, value)
    await session.commit()
    await session.refresh(product)
    return ProductOut.model_validate(product)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_product(product_id: uuid.UUID, session: DbSession) -> Response:
    """Deactivates: suggestions and approvals keep referencing the product."""
    product = await _get_product(session, product_id)
    product.active = False
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class AffiliateLinkOut(BaseModel):
    id: uuid.UUID
    clip_id: uuid.UUID
    product: ProductOut
    relevance_score: float
    match_reason: str | None
    status: str
    is_primary: bool
    reviewed_at: datetime | None
    created_at: datetime


class AffiliateLinkList(BaseModel):
    enabled: bool
    items: list[AffiliateLinkOut]


async def clip_links(session: DbSession, clip_id: uuid.UUID) -> AffiliateLinkList:
    """Approved first, then suggestions by relevance; rejected last."""
    order = case({"approved": 0, "suggested": 1}, value=ClipAffiliateLink.status, else_=2)
    rows = await session.execute(
        select(ClipAffiliateLink, AffiliateProduct)
        .join(AffiliateProduct, AffiliateProduct.id == ClipAffiliateLink.product_id)
        .where(ClipAffiliateLink.clip_id == clip_id)
        .order_by(order, ClipAffiliateLink.relevance_score.desc(), ClipAffiliateLink.id)
    )
    return AffiliateLinkList(
        enabled=await affiliate_links_enabled(session),
        items=[
            AffiliateLinkOut(
                id=link.id,
                clip_id=link.clip_id,
                product=ProductOut.model_validate(product),
                relevance_score=float(link.relevance_score),
                match_reason=link.match_reason,
                status=link.status,
                is_primary=link.is_primary,
                reviewed_at=link.reviewed_at,
                created_at=link.created_at,
            )
            for link, product in rows
        ],
    )


async def existing_clip(session: DbSession, clip_id: uuid.UUID) -> Clip:
    clip = await session.get(Clip, clip_id)
    if clip is None:
        raise NotFound("clip not found")
    return clip


async def _api_embedding(
    session: DbSession, sessions: SessionsDep, http: httpx.AsyncClient, job_id: uuid.UUID
) -> EmbeddingSetup | None:
    try:
        snapshot = (await resolve_providers(session, (await load_settings(session)).models))[
            "embedding"
        ]
        provider = provider_registry(http, QuotaGuard(sessions)).embedding(snapshot)
    except (ProviderConfigError, KeyError) as exc:
        logger.warning("affiliate embeddings unavailable", extra={"reason": str(exc)})
        return None
    record = usage_recorder(sessions, provider=snapshot, job_id=job_id, stage="affiliate_matching")
    return EmbeddingSetup(provider, snapshot.model, record)


@clip_links_router.post("/rematch")
async def rematch_clip(
    clip_id: uuid.UUID, session: DbSession, sessions: SessionsDep
) -> AffiliateLinkList:
    """Replaces the open suggestions; approved and rejected links are kept."""
    clip = await existing_clip(session, clip_id)
    if not await affiliate_links_enabled(session):
        raise Conflict("affiliate links are disabled")
    async with httpx.AsyncClient() as http:
        embedding = await _api_embedding(session, sessions, http, clip.job_id)
        await AffiliateMatcher(sessions, embedding).match_clip(clip_id, replace_suggested=True)
    return await clip_links(session, clip_id)


class LinkReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    primary: bool | None = None

    @model_validator(mode="after")
    def _primary_needs_approval(self) -> Self:
        if self.primary and self.decision != "approved":
            raise ValueError("only an approved link can be primary")
        return self


class ManualLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    primary: bool | None = None


@clip_links_router.get("")
async def list_clip_links(clip_id: uuid.UUID, session: DbSession) -> AffiliateLinkList:
    await existing_clip(session, clip_id)
    return await clip_links(session, clip_id)


@clip_links_router.post("", status_code=status.HTTP_201_CREATED)
async def select_product(
    clip_id: uuid.UUID, body: ManualLinkIn, admin: CurrentAdmin, session: DbSession
) -> AffiliateLinkList:
    await existing_clip(session, clip_id)
    if not await affiliate_links_enabled(session):
        raise Conflict("affiliate links are disabled")
    try:
        await add_manual_link(session, clip_id, body.product_id, admin.id, body.primary)
    except ProductUnavailable:
        raise Unprocessable("the product does not exist or is inactive") from None
    except AlreadyLinked:
        raise Conflict("the product is already linked to this clip") from None
    return await clip_links(session, clip_id)


@clip_links_router.post("/{link_id}/review")
async def review_clip_link(
    clip_id: uuid.UUID,
    link_id: uuid.UUID,
    body: LinkReviewIn,
    admin: CurrentAdmin,
    session: DbSession,
) -> AffiliateLinkList:
    """Rejecting works with the toggle off; approving needs it on."""
    await existing_clip(session, clip_id)
    if body.decision == "approved" and not await affiliate_links_enabled(session):
        raise Conflict("affiliate links are disabled")
    try:
        await review_link(session, clip_id, link_id, admin.id, body.decision, body.primary)
    except LinkNotFound:
        raise NotFound("affiliate link not found") from None
    return await clip_links(session, clip_id)


@clip_links_router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_clip_link(
    clip_id: uuid.UUID, link_id: uuid.UUID, admin: CurrentAdmin, session: DbSession
) -> Response:
    """Removal is allowed even with the toggle off (spec: records kept until removed)."""
    try:
        await remove_link(session, clip_id, link_id, admin.id)
    except LinkNotFound:
        raise NotFound("affiliate link not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
