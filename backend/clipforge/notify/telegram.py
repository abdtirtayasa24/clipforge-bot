"""Telegram Bot API `sendMessage` for admin notifications."""

import httpx

from clipforge.config import Settings

API_URL = "https://api.telegram.org"
HTTP_TIMEOUT_S = 15.0
MAX_MESSAGE_CHARS = 4096


class NotifyError(Exception):
    pass


def telegram_client(settings: Settings, http: httpx.AsyncClient) -> "TelegramClient | None":
    if settings.telegram_bot_token is None:
        return None
    return TelegramClient(http, settings.telegram_bot_token.get_secret_value())


class TelegramClient:
    def __init__(self, http: httpx.AsyncClient, bot_token: str) -> None:
        self._http = http
        self._url = f"{API_URL}/bot{bot_token}/sendMessage"

    async def send_message(self, chat_id: int, text: str) -> None:
        """Plain text only: no parse mode, so titles from the model cannot inject markup."""
        try:
            response = await self._http.post(
                self._url,
                json={
                    "chat_id": chat_id,
                    "text": text[:MAX_MESSAGE_CHARS],
                    "disable_web_page_preview": True,
                },
                timeout=HTTP_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"Telegram unreachable: {type(exc).__name__}") from None
        if not response.is_success:
            raise NotifyError(f"Telegram sendMessage failed with HTTP {response.status_code}")
