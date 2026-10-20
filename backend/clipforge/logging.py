import json
import logging
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = (
    r"[\w-]*token|[\w-]*secret|password|passwd|api[_-]?key|init[_-]?data|authorization"
    r"|cookie|set-cookie|signature|hash|code|session"
)
_KEY_VALUE = re.compile(
    rf"""(?ix)
    (?P<key>["']?\b(?:{_SENSITIVE_KEYS})["']?\s*[:=]\s*)
    (?P<value>
        (?:bearer|basic)\s+[^\s"',;&]+
      | "[^"]*"
      | '[^']*'
      | [^\s"',;&]+
    )
    """
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
# The Bot API puts the bot token in the URL path: https://api.telegram.org/bot<token>/method
_TELEGRAM_BOT_PATH = re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+")
_URL_QUERY = re.compile(r"(?i)((?:\bhttps?://|(?<![\w/:.])/)[^\s?#\"']*\?)[^\s#\"']+")

_CONTEXT_FIELDS = ("request_id", "job_id", "stage", "attempt")
_context: ContextVar[dict[str, Any] | None] = ContextVar("clipforge_log_context", default=None)

_RESERVED_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}
_SENSITIVE_EXTRA_KEY = re.compile(rf"(?i)^(?:{_SENSITIVE_KEYS})$")


def redact(text: str) -> str:
    text = _URL_QUERY.sub(rf"\1{REDACTED}", text)
    text = _TELEGRAM_BOT_PATH.sub(rf"\1{REDACTED}", text)
    text = _KEY_VALUE.sub(rf"\g<key>{REDACTED}", text)
    return _BEARER.sub(rf"\1 {REDACTED}", text)


def _redact_value(key: str, value: Any) -> Any:
    if _SENSITIVE_EXTRA_KEY.match(key):
        return REDACTED
    if isinstance(value, str):
        return redact(value)
    return value


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    unknown = set(fields) - set(_CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"unknown log context fields: {sorted(unknown)}")
    merged = {**(_context.get() or {}), **fields}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def _stamp_context(factory: Callable[..., logging.LogRecord]) -> Callable[..., logging.LogRecord]:
    # Copy the context onto the record when it is created, so handlers that format later
    # (or on another thread) still see the request/job it belongs to.
    def create(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        record._clipforge_context = _context.get() or {}
        return record

    create.clipforge_context = True  # type: ignore[attr-defined]
    return create


def install_context_record_factory() -> None:
    factory = logging.getLogRecordFactory()
    if not getattr(factory, "clipforge_context", False):
        logging.setLogRecordFactory(_stamp_context(factory))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        payload.update(getattr(record, "_clipforge_context", {}))
        for key, value in vars(record).items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = _redact_value(key, value)
        if record.exc_info:
            payload["exc_info"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    install_context_record_factory()
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
