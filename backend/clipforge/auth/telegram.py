import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

# Telegram clients and servers disagree slightly on clocks; tolerate a small future skew.
_MAX_CLOCK_SKEW = timedelta(seconds=60)


class InvalidInitData(Exception):
    """initData failed verification. Messages never include the submitted data."""


@dataclass(frozen=True)
class TelegramUser:
    id: int


def _parse_fields(init_data: str) -> dict[str, str]:
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise InvalidInitData("malformed initData") from None
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise InvalidInitData("duplicate initData fields")
    return fields


def _verify_hash(fields: dict[str, str], bot_token: str) -> None:
    received_hash = fields.pop("hash", "")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise InvalidInitData("initData signature mismatch")


def _verify_freshness(fields: dict[str, str], max_age: timedelta, now: datetime) -> None:
    try:
        auth_date = datetime.fromtimestamp(int(fields["auth_date"]), UTC)
    except (KeyError, ValueError, OverflowError):
        raise InvalidInitData("missing or invalid auth_date") from None
    if auth_date > now + _MAX_CLOCK_SKEW or now - auth_date > max_age:
        raise InvalidInitData("initData is not fresh")


def _parse_user(fields: dict[str, str]) -> TelegramUser:
    try:
        user = json.loads(fields["user"])
    except (KeyError, ValueError):
        raise InvalidInitData("missing or invalid user") from None
    user_id = user.get("id") if isinstance(user, dict) else None
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise InvalidInitData("missing or invalid user id")
    return TelegramUser(id=user_id)


def verify_init_data(
    init_data: str, bot_token: str, *, max_age: timedelta, now: datetime
) -> TelegramUser:
    fields = _parse_fields(init_data)
    _verify_hash(fields, bot_token)
    _verify_freshness(fields, max_age, now)
    return _parse_user(fields)
