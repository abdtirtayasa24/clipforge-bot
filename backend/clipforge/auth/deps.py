import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from clipforge.api.deps import DbSession, SettingsDep
from clipforge.auth.session import InvalidSessionToken, verify_session_token
from clipforge.db.models import AdminUser

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "invalid or expired session",
        headers={"WWW-Authenticate": "Bearer"},
    )


def session_secret(settings: SettingsDep) -> str:
    if settings.session_secret is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth is not configured")
    return settings.session_secret.get_secret_value()


async def require_admin(
    session: DbSession,
    secret: Annotated[str, Depends(session_secret)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AdminUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    try:
        claims = verify_session_token(credentials.credentials, secret, now=datetime.now(UTC))
    except InvalidSessionToken as exc:
        logger.info("session rejected", extra={"reason": str(exc)})
        raise _unauthorized() from None
    admin = await session.scalar(
        select(AdminUser).where(AdminUser.id == claims.admin_user_id, AdminUser.active)
    )
    if admin is None:
        logger.info("session rejected", extra={"reason": "admin missing or inactive"})
        raise _unauthorized()
    return admin


CurrentAdmin = Annotated[AdminUser, Depends(require_admin)]
