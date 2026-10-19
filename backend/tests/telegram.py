import hashlib
import hmac
import json
from datetime import datetime
from urllib.parse import urlencode

BOT_TOKEN = "123456:TEST-bot-token"


def sign_init_data(
    *,
    user_id: int,
    auth_date: datetime,
    bot_token: str = BOT_TOKEN,
    extra: dict[str, str] | None = None,
) -> str:
    """Builds Mini App initData signed as described in the Telegram Bot API docs."""
    fields = {
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Admin"}, separators=(",", ":")),
        "auth_date": str(int(auth_date.timestamp())),
        **(extra or {}),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
