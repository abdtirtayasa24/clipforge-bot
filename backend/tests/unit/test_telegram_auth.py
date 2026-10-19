import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode

import pytest

from clipforge.auth.rate_limit import FailedAttemptLimiter
from clipforge.auth.session import (
    InvalidSessionToken,
    issue_session_token,
    verify_session_token,
)
from clipforge.auth.telegram import InvalidInitData, verify_init_data
from tests.telegram import BOT_TOKEN, sign_init_data

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
MAX_AGE = timedelta(hours=1)
SECRET = "s" * 32


def verify(init_data: str, now: datetime = NOW) -> int:
    return verify_init_data(init_data, BOT_TOKEN, max_age=MAX_AGE, now=now).id


def replace_field(init_data: str, key: str, value: str) -> str:
    return urlencode([(k, value if k == key else v) for k, v in parse_qsl(init_data)])


def test_valid_init_data_returns_user() -> None:
    init_data = sign_init_data(user_id=42, auth_date=NOW - timedelta(minutes=5))

    assert verify(init_data) == 42


def test_extra_signed_fields_are_part_of_the_check() -> None:
    init_data = sign_init_data(
        user_id=42, auth_date=NOW, extra={"signature": "abc", "chat_type": "private"}
    )

    assert verify(init_data) == 42


@pytest.mark.parametrize(
    "tamper",
    [
        lambda d: replace_field(d, "hash", "0" * 64),
        lambda d: replace_field(d, "user", '{"id":1,"first_name":"Mallory"}'),
        lambda d: replace_field(d, "auth_date", str(int(NOW.timestamp()))),
        lambda d: urlencode([(k, v) for k, v in parse_qsl(d) if k != "hash"]),
        lambda d: d + "&user=%7B%22id%22%3A1%7D",
        lambda d: "not-a-query-string",
        lambda d: "",
    ],
)
def test_tampered_init_data_is_rejected(tamper: object) -> None:
    init_data = sign_init_data(user_id=42, auth_date=NOW - timedelta(minutes=1))

    with pytest.raises(InvalidInitData):
        verify(tamper(init_data))  # type: ignore[operator]


def test_init_data_signed_for_another_bot_is_rejected() -> None:
    init_data = sign_init_data(user_id=42, auth_date=NOW, bot_token="999:other")

    with pytest.raises(InvalidInitData):
        verify(init_data)


@pytest.mark.parametrize(
    "auth_date",
    [NOW - MAX_AGE - timedelta(seconds=1), NOW + timedelta(minutes=5)],
)
def test_stale_or_future_auth_date_is_rejected(auth_date: datetime) -> None:
    with pytest.raises(InvalidInitData):
        verify(sign_init_data(user_id=42, auth_date=auth_date))


@pytest.mark.parametrize("user", ["not json", '{"first_name":"x"}', '{"id":"42"}', "[]"])
def test_malformed_user_is_rejected(user: str) -> None:
    with pytest.raises(InvalidInitData):
        verify(sign_init_data(user_id=42, auth_date=NOW, extra={"user": user}))


def test_error_message_never_echoes_init_data() -> None:
    init_data = sign_init_data(user_id=42, auth_date=NOW - timedelta(days=1))

    with pytest.raises(InvalidInitData) as exc_info:
        verify(init_data)

    hash_value = dict(parse_qsl(init_data))["hash"]
    assert init_data not in str(exc_info.value)
    assert hash_value not in str(exc_info.value)


ADMIN_ID = uuid.uuid4()


def test_session_token_round_trip() -> None:
    token, expires_at = issue_session_token(ADMIN_ID, 42, SECRET, now=NOW)

    claims = verify_session_token(token, SECRET, now=NOW + timedelta(hours=11))

    assert claims.admin_user_id == ADMIN_ID
    assert claims.telegram_user_id == 42
    assert expires_at == NOW + timedelta(hours=12)


def test_expired_session_token_is_rejected() -> None:
    token, _ = issue_session_token(ADMIN_ID, 42, SECRET, now=NOW)

    with pytest.raises(InvalidSessionToken):
        verify_session_token(token, SECRET, now=NOW + timedelta(hours=12))


def test_session_token_signed_with_other_secret_is_rejected() -> None:
    token, _ = issue_session_token(ADMIN_ID, 42, "o" * 32, now=NOW)

    with pytest.raises(InvalidSessionToken):
        verify_session_token(token, SECRET, now=NOW)


def test_session_token_with_tampered_payload_is_rejected() -> None:
    token, _ = issue_session_token(ADMIN_ID, 42, SECRET, now=NOW)
    other, _ = issue_session_token(uuid.uuid4(), 1, SECRET, now=NOW)
    version, _, signature = token.split(".")
    _, other_payload, _ = other.split(".")

    with pytest.raises(InvalidSessionToken):
        verify_session_token(f"{version}.{other_payload}.{signature}", SECRET, now=NOW)


@pytest.mark.parametrize("token", ["", "abc", "v1.x.y", "v2.a.b", "v1..", "v1.e30.e30"])
def test_malformed_session_tokens_are_rejected(token: str) -> None:
    with pytest.raises(InvalidSessionToken):
        verify_session_token(token, SECRET, now=NOW)


def test_session_secret_must_be_long_enough() -> None:
    with pytest.raises(ValueError):
        issue_session_token(ADMIN_ID, 42, "short", now=NOW)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_blocks_after_max_failures_within_window() -> None:
    clock = FakeClock()
    limiter = FailedAttemptLimiter(max_failures=3, window_seconds=60, clock=clock)

    for _ in range(3):
        assert not limiter.is_blocked("1.2.3.4")
        limiter.record_failure("1.2.3.4")

    assert limiter.is_blocked("1.2.3.4")
    assert not limiter.is_blocked("5.6.7.8")


def test_limiter_forgets_failures_after_window() -> None:
    clock = FakeClock()
    limiter = FailedAttemptLimiter(max_failures=2, window_seconds=60, clock=clock)
    limiter.record_failure("ip")
    limiter.record_failure("ip")

    clock.now += 61

    assert not limiter.is_blocked("ip")
