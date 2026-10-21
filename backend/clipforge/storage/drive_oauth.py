"""Google Drive OAuth (ADR-015): consent with a single-use state, an encrypted refresh token,
and access tokens refreshed from it."""

import base64
import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.db.models import AppSetting, OauthCredential
from clipforge.queue.errors import TransientError
from clipforge.queue.repository import Clock, utc_now
from clipforge.security.crypto import DecryptionError, TokenCipher

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint, not a secret
ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
PROVIDER = "google_drive"
STATE_KEY = "drive_oauth_state"
# With the drive.file scope the app only sees files and folders it created itself, so uploads
# go to a folder the app creates; its id is kept under FOLDER_KEY.
FOLDER_KEY = "drive_folder"
FOLDER_NAME = "ClipForge Bot"
STATE_TTL = timedelta(minutes=10)
# Refresh a little before Google's expiry so a token never lapses mid-request.
EXPIRY_MARGIN = timedelta(minutes=2)
HTTP_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str
    redirect_uri: str


class InvalidState(Exception):
    pass


class OAuthExchangeError(Exception):
    pass


class DriveNotConnected(TransientError):
    """No stored Drive credential (or no OAuth client configured); retried after reconnecting."""


class DriveAuthExpired(TransientError):
    """Google rejected the refresh token (revoked, expired or the client changed)."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def begin_consent(
    sessions: async_sessionmaker[AsyncSession],
    client: OAuthClient,
    admin_user_id: uuid.UUID,
    *,
    clock: Clock = utc_now,
) -> str:
    """The Google consent URL. Only a hash of `state` is stored; a newer connect attempt
    replaces an older pending one."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    pending = {
        "state_sha256": _sha256(state),
        "code_verifier": verifier,
        "admin_user_id": str(admin_user_id),
        "expires_at": (clock() + STATE_TTL).timestamp(),
    }
    async with sessions.begin() as session:
        await session.execute(
            insert(AppSetting)
            .values(key=STATE_KEY, value=pending)
            .on_conflict_do_update(index_elements=["key"], set_={"value": pending})
        )
    query = {
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uri,
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "access_type": "offline",
        # Google only returns a refresh token on consent, so always ask for it.
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": _code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(query)}"


async def _consume_state(
    sessions: async_sessionmaker[AsyncSession], state: str, clock: Clock
) -> dict[str, Any]:
    async with sessions.begin() as session:
        pending = await session.scalar(
            delete(AppSetting)
            .where(
                AppSetting.key == STATE_KEY,
                AppSetting.value["state_sha256"].astext == _sha256(state),
            )
            .returning(AppSetting.value)
        )
    if not isinstance(pending, dict) or float(pending["expires_at"]) < clock().timestamp():
        raise InvalidState("unknown, used or expired state")
    return pending


async def _post_token(http: httpx.AsyncClient, data: dict[str, str]) -> httpx.Response:
    try:
        return await http.post(TOKEN_URL, data=data, timeout=HTTP_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise TransientError(f"Google token endpoint unreachable: {type(exc).__name__}") from None


async def _account_email(http: httpx.AsyncClient, access_token: str) -> str | None:
    try:
        response = await http.get(
            ABOUT_URL,
            params={"fields": "user(emailAddress)"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT_S,
        )
        email = response.json()["user"]["emailAddress"] if response.is_success else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        email = None
    return email if isinstance(email, str) else None


async def complete_consent(
    sessions: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    client: OAuthClient,
    cipher: TokenCipher,
    *,
    state: str,
    code: str,
    clock: Clock = utc_now,
) -> None:
    pending = await _consume_state(sessions, state, clock)
    response = await _post_token(
        http,
        {
            "code": code,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "redirect_uri": client.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": str(pending["code_verifier"]),
        },
    )
    if not response.is_success:
        logger.warning("drive code exchange rejected", extra={"status": response.status_code})
        raise OAuthExchangeError("Google rejected the authorization code")
    body = response.json()
    scopes = str(body.get("scope", "")).split()
    if DRIVE_SCOPE not in scopes:
        raise OAuthExchangeError("the drive.file scope was not granted")
    refresh_token = body.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise OAuthExchangeError("Google returned no refresh token")
    values = {
        "encrypted_refresh_token": cipher.encrypt(refresh_token),
        "scopes": scopes,
        "account_email": await _account_email(http, str(body.get("access_token", ""))),
    }
    async with sessions.begin() as session:
        await session.execute(
            insert(OauthCredential)
            .values(provider=PROVIDER, **values)
            .on_conflict_do_update(index_elements=[OauthCredential.provider], set_=values)
        )
    logger.info("google drive connected", extra={"admin_user_id": pending["admin_user_id"]})


class DriveTokens:
    """Access tokens from the stored refresh token, cached until shortly before expiry."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        http: httpx.AsyncClient,
        client: OAuthClient,
        cipher: TokenCipher,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._http = http
        self._client = client
        self._cipher = cipher
        self._clock = clock
        self._token: str | None = None
        self._expires_at: datetime | None = None

    async def access_token(self) -> str:
        if self._token and self._expires_at and self._clock() < self._expires_at:
            return self._token
        async with self._sessions() as session:
            encrypted = await session.scalar(
                select(OauthCredential.encrypted_refresh_token).where(
                    OauthCredential.provider == PROVIDER
                )
            )
        if encrypted is None:
            raise DriveNotConnected("Google Drive is not connected")
        try:
            refresh_token = self._cipher.decrypt(encrypted)
        except DecryptionError:
            # Expected after rotating the encryption key: reconnecting stores a fresh token.
            raise DriveAuthExpired(
                "the stored Drive credential uses another encryption key; reconnect Drive"
            ) from None
        response = await _post_token(
            self._http,
            {
                "refresh_token": refresh_token,
                "client_id": self._client.client_id,
                "client_secret": self._client.client_secret,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code in (400, 401):
            raise DriveAuthExpired("Google Drive authorization expired; reconnect Drive")
        if not response.is_success:
            raise TransientError(f"Google token refresh failed with HTTP {response.status_code}")
        body = response.json()
        self._token = str(body["access_token"])
        self._expires_at = (
            self._clock() + timedelta(seconds=int(body.get("expires_in", 3600))) - EXPIRY_MARGIN
        )
        return self._token

    def invalidate(self) -> None:
        """Forgets the cached access token after Drive rejected it."""
        self._token = None
        self._expires_at = None
