from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select

from clipforge.api.deps import DbSession, SessionsDep, SettingsDep
from clipforge.auth.deps import CurrentAdmin, require_admin
from clipforge.config import Settings
from clipforge.db.app_settings import get_setting
from clipforge.db.models import OauthCredential
from clipforge.queue.errors import TransientError
from clipforge.security.crypto import TokenCipher
from clipforge.storage.drive_oauth import (
    FOLDER_KEY,
    FOLDER_NAME,
    PROVIDER,
    DriveAuthExpired,
    DriveTokens,
    InvalidState,
    OAuthClient,
    OAuthExchangeError,
    begin_consent,
    complete_consent,
)

router = APIRouter(prefix="/integrations/drive", tags=["integrations"])

ConnectionStatus = Literal["disconnected", "connected", "expired", "unavailable"]

_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'><title>ClipForge Bot</title></head>"
    "<body><p>{message}</p></body></html>"
)


def oauth_client(settings: SettingsDep) -> OAuthClient:
    if not (
        settings.google_client_id and settings.google_client_secret and settings.drive_redirect_uri
    ):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google OAuth is not configured")
    return OAuthClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        redirect_uri=settings.drive_redirect_uri,
    )


def token_cipher(settings: SettingsDep) -> TokenCipher:
    if settings.token_encryption_key is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "token encryption key is not configured"
        )
    return TokenCipher(settings.token_encryption_key.get_secret_value())


OAuthClientDep = Annotated[OAuthClient, Depends(oauth_client)]
CipherDep = Annotated[TokenCipher, Depends(token_cipher)]


class ConnectOut(BaseModel):
    auth_url: str


class DriveStatus(BaseModel):
    status: ConnectionStatus
    scopes: list[str]
    account_email: str | None
    target_folder_name: str
    target_folder_id: str | None


@router.get("/connect")
async def connect(admin: CurrentAdmin, sessions: SessionsDep, client: OAuthClientDep) -> ConnectOut:
    return ConnectOut(auth_url=await begin_consent(sessions, client, admin.id))


def _page(message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(message=message), status_code=status_code)


@router.get("/callback", response_class=HTMLResponse)
async def callback(
    sessions: SessionsDep,
    client: OAuthClientDep,
    cipher: CipherDep,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Google redirects the admin's browser here, so there is no bearer token: the single-use
    state, issued to an authenticated admin, is what authorizes the request (ADR-016)."""
    if error or not state or not code:
        return _page("Google Drive was not connected. Start again from the app.", 400)
    try:
        async with httpx.AsyncClient() as http:
            await complete_consent(sessions, http, client, cipher, state=state, code=code)
    except (InvalidState, OAuthExchangeError):
        return _page("Google Drive was not connected. Start again from the app.", 400)
    except TransientError:
        return _page("Google could not be reached. Try again in a moment.", 503)
    return _page("Google Drive is connected. You can close this window.", 200)


async def _connection_status(
    settings: Settings, sessions: SessionsDep, client: OAuthClient
) -> ConnectionStatus:
    try:
        cipher = token_cipher(settings)
        async with httpx.AsyncClient() as http:
            await DriveTokens(sessions, http, client, cipher).access_token()
    except DriveAuthExpired:
        return "expired"
    except (TransientError, HTTPException):
        return "unavailable"
    return "connected"


@router.get("/status", dependencies=[Depends(require_admin)])
async def drive_status(
    session: DbSession, sessions: SessionsDep, settings: SettingsDep, client: OAuthClientDep
) -> DriveStatus:
    credential = await session.scalar(
        select(OauthCredential).where(OauthCredential.provider == PROVIDER)
    )
    folder = await get_setting(session, FOLDER_KEY)
    folder_id = folder.get("id") if isinstance(folder, dict) else None
    connection: ConnectionStatus = "disconnected"
    if credential is not None:
        connection = await _connection_status(settings, sessions, client)
    return DriveStatus(
        status=connection,
        scopes=list(credential.scopes) if credential else [],
        account_email=credential.account_email if credential else None,
        target_folder_name=FOLDER_NAME,
        target_folder_id=folder_id if isinstance(folder_id, str) else None,
    )
