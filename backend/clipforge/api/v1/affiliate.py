import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import func, select

from clipforge.affiliate.validation import LinkRejected, check_affiliate_url, destination_host
from clipforge.api.deps import DbSession
from clipforge.api.errors import NotFound, Unprocessable
from clipforge.auth.deps import require_admin
from clipforge.db.models import AffiliateProduct
from clipforge.discovery.regions import ISO_3166_ALPHA2
from clipforge.settings_schema import load_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/affiliate", tags=["affiliate"], dependencies=[Depends(require_admin)])

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
