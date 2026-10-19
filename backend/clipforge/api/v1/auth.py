import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from clipforge.api.deps import DbSession, SettingsDep
from clipforge.auth.deps import session_secret
from clipforge.auth.rate_limit import FailedAttemptLimiter
from clipforge.auth.session import issue_session_token
from clipforge.auth.telegram import InvalidInitData, verify_init_data
from clipforge.db.models import AdminUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramAuthRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=4096)


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_at: datetime


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/telegram")
async def authenticate_telegram(
    body: TelegramAuthRequest,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    secret: Annotated[str, Depends(session_secret)],
) -> SessionResponse:
    if settings.telegram_bot_token is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth is not configured")
    limiter: FailedAttemptLimiter = request.app.state.auth_limiter
    client = _client_key(request)
    if limiter.is_blocked(client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many failed attempts")

    now = datetime.now(UTC)
    try:
        user = verify_init_data(
            body.init_data,
            settings.telegram_bot_token.get_secret_value(),
            max_age=timedelta(seconds=settings.init_data_max_age_seconds),
            now=now,
        )
    except InvalidInitData as exc:
        limiter.record_failure(client)
        logger.warning("telegram auth rejected", extra={"reason": str(exc)})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid initData") from None

    admin = await session.scalar(
        select(AdminUser).where(AdminUser.telegram_user_id == user.id, AdminUser.active)
    )
    if admin is None:
        limiter.record_failure(client)
        logger.warning("telegram auth rejected", extra={"reason": "not an active admin"})
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not an administrator")

    token, expires_at = issue_session_token(admin.id, user.id, secret, now=now)
    logger.info("admin authenticated", extra={"admin_user_id": str(admin.id)})
    return SessionResponse(access_token=token, expires_at=expires_at)
