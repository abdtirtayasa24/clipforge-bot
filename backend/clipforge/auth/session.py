import base64
import binascii
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

SESSION_TTL = timedelta(hours=12)
MIN_SECRET_LENGTH = 32
_VERSION = "v1"


class InvalidSessionToken(Exception):
    pass


@dataclass(frozen=True)
class SessionClaims:
    admin_user_id: uuid.UUID
    telegram_user_id: int
    expires_at: datetime


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str, secret: str) -> str:
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"session secret must be at least {MIN_SECRET_LENGTH} characters")
    message = f"{_VERSION}.{payload}".encode()
    return _b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest())


def issue_session_token(
    admin_user_id: uuid.UUID, telegram_user_id: int, secret: str, *, now: datetime
) -> tuple[str, datetime]:
    expires_at = now + SESSION_TTL
    claims = {
        "sub": str(admin_user_id),
        "tg": telegram_user_id,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    payload = _b64encode(json.dumps(claims, separators=(",", ":")).encode())
    return f"{_VERSION}.{payload}.{_sign(payload, secret)}", expires_at


def verify_session_token(token: str, secret: str, *, now: datetime) -> SessionClaims:
    version, _, rest = token.partition(".")
    payload, _, signature = rest.partition(".")
    if version != _VERSION or not payload or not signature:
        raise InvalidSessionToken("malformed session token")
    if not hmac.compare_digest(_sign(payload, secret), signature):
        raise InvalidSessionToken("session token signature mismatch")
    try:
        claims = json.loads(_b64decode(payload))
        session = SessionClaims(
            admin_user_id=uuid.UUID(claims["sub"]),
            telegram_user_id=int(claims["tg"]),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
        )
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise InvalidSessionToken("malformed session claims") from None
    if session.expires_at <= now:
        raise InvalidSessionToken("session token expired")
    return session
