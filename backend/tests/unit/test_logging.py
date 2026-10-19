import io
import json
import logging
from collections.abc import Iterator

import pytest

from clipforge.logging import REDACTED, configure_logging, log_context, redact


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging(level="DEBUG", stream=stream)
    yield stream
    logging.getLogger().handlers.clear()


def read_records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_each_line_is_a_json_object_with_base_fields(log_stream: io.StringIO) -> None:
    logging.getLogger("clipforge.test").info("hello %s", "world")

    [record] = read_records(log_stream)
    assert record["level"] == "INFO"
    assert record["logger"] == "clipforge.test"
    assert record["msg"] == "hello world"
    assert isinstance(record["ts"], str)
    assert "request_id" not in record


def test_context_fields_are_attached_only_inside_context(log_stream: io.StringIO) -> None:
    logger = logging.getLogger("clipforge.test")

    with log_context(request_id="req-1", job_id="job-1", stage="transcribe", attempt=2):
        logger.info("inside")
    logger.info("outside")

    inside, outside = read_records(log_stream)
    assert inside["request_id"] == "req-1"
    assert inside["job_id"] == "job-1"
    assert inside["stage"] == "transcribe"
    assert inside["attempt"] == 2
    assert "job_id" not in outside


def test_nested_context_restores_outer_values(log_stream: io.StringIO) -> None:
    logger = logging.getLogger("clipforge.test")

    with log_context(job_id="job-1", stage="download"):
        with log_context(stage="render"):
            logger.info("inner")
        logger.info("outer")

    inner, outer = read_records(log_stream)
    assert inner["stage"] == "render"
    assert inner["job_id"] == "job-1"
    assert outer["stage"] == "download"


def test_extra_fields_are_included(log_stream: io.StringIO) -> None:
    logging.getLogger("clipforge.test").info("done", extra={"duration_ms": 12})

    [record] = read_records(log_stream)
    assert record["duration_ms"] == 12


def test_uvicorn_color_message_is_dropped(log_stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").info("started", extra={"color_message": "\x1b[36mx"})

    [record] = read_records(log_stream)
    assert "color_message" not in record


def test_exception_is_serialized(log_stream: io.StringIO) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("clipforge.test").exception("failed")

    [record] = read_records(log_stream)
    assert "ValueError: boom" in str(record["exc_info"])


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("authorization=Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("token=s3cr3t-value&x=1", "s3cr3t-value"),
        ('{"access_token": "ya29.zzz"}', "ya29.zzz"),
        ("client_secret: hunter2", "hunter2"),
        ("initData=query_id%3DAAH%26hash%3Ddeadbeef", "deadbeef"),
        ("GET https://example.com/cb?code=4/abc&state=xyz HTTP/1.1", "4/abc"),
        ("refresh_token='1//refresh'", "1//refresh"),
        ("password=pa55", "pa55"),
        ('127.0.0.1:5 - "GET /api/v1/x?v=priv HTTP/1.1" 200', "priv"),
        ("Cookie: session=abc123", "abc123"),
    ],
)
def test_redact_masks_sensitive_values(raw: str, secret: str) -> None:
    masked = redact(raw)

    assert secret not in masked
    assert REDACTED in masked


def test_redact_keeps_url_path_and_plain_text() -> None:
    masked = redact("fetched https://example.com/watch?v=abc in 3s")

    assert masked.startswith("fetched https://example.com/watch?")
    assert "abc" not in masked
    assert masked.endswith(" in 3s")
    assert redact("plain message") == "plain message"
    assert redact("status_code=500 error_code=E1") == "status_code=500 error_code=E1"


def test_secrets_never_reach_the_log_output(log_stream: io.StringIO) -> None:
    logger = logging.getLogger("clipforge.test")

    logger.info("auth with Bearer %s", "tok-123")
    logger.warning("init", extra={"init_data": "hash=abc", "authorization": "Bearer tok-456"})
    with log_context(request_id="r"):
        logger.error("callback https://x.test/cb?code=tok-789")

    output = log_stream.getvalue()
    for secret in ("tok-123", "hash=abc", "tok-456", "tok-789"):
        assert secret not in output
    assert all(isinstance(r, dict) for r in read_records(log_stream))
