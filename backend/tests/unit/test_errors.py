import logging
from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from clipforge.api.errors import ApiError
from clipforge.logging import JsonFormatter
from clipforge.main import create_app
from tests.api import api_settings, client_for


class SecretBody(BaseModel):
    init_data: str = Field(max_length=100)


class QuotaExhausted(ApiError):
    status_code = 409
    code = "quota_exhausted"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(api_settings())

    @app.get("/api/v1/_test/http-error")
    async def http_error() -> None:
        raise HTTPException(401, "nope", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/api/v1/_test/domain-error")
    async def domain_error() -> None:
        raise QuotaExhausted("daily quota used", details={"retry_after_s": 60})

    @app.get("/api/v1/_test/crash")
    async def crash() -> None:
        raise RuntimeError("database password is hunter2")

    @app.post("/api/v1/_test/validate")
    async def validate(body: SecretBody) -> None:
        pass

    @app.get("/api/v1/_test/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    with client_for(app) as test_client:
        yield test_client


def test_unknown_route_uses_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": None}
    }


def test_http_exception_uses_envelope_and_keeps_headers(client: TestClient) -> None:
    response = client.get("/api/v1/_test/http-error")

    assert response.status_code == 401
    assert response.json()["error"] == {"code": "unauthorized", "message": "nope", "details": None}
    assert response.headers["www-authenticate"] == "Bearer"


def test_domain_error_uses_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/_test/domain-error")

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "quota_exhausted",
            "message": "daily quota used",
            "details": {"retry_after_s": 60},
        }
    }


def test_validation_error_uses_envelope_without_echoing_input(client: TestClient) -> None:
    secret_input = "query_id=AAH&hash=" + "f" * 5000

    response = client.post("/api/v1/_test/validate", json={"init_data": secret_input})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "init_data"]
    assert "input" not in error["details"][0]
    assert secret_input not in response.text


def test_unhandled_error_is_generic_and_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    response = client.get("/api/v1/_test/crash", headers={"X-Request-ID": "req-crash"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "internal server error", "details": None}
    }
    assert "hunter2" not in response.text
    assert response.headers["x-request-id"] == "req-crash"
    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error.exc_info is not None


def test_request_id_is_generated_and_returned(client: TestClient) -> None:
    response = client.get("/api/v1/_test/ok")

    assert len(response.headers["x-request-id"]) == 32


def test_valid_incoming_request_id_is_kept(client: TestClient) -> None:
    response = client.get("/api/v1/_test/ok", headers={"X-Request-ID": "abc-123_x.y"})

    assert response.headers["x-request-id"] == "abc-123_x.y"


@pytest.mark.parametrize("incoming", ["has space", "x" * 65, "semi;colon"])
def test_invalid_incoming_request_id_is_replaced(client: TestClient, incoming: str) -> None:
    response = client.get("/api/v1/_test/ok", headers={"X-Request-ID": incoming})

    assert response.headers["x-request-id"] != incoming


def test_access_log_has_request_fields_without_query_string(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    client.get(
        "/api/v1/_test/ok?token=tok-secret",
        headers={"X-Request-ID": "req-1", "Authorization": "Bearer tok-header"},
    )

    [access] = [r for r in caplog.records if r.name == "clipforge.access"]
    assert access.method == "GET"  # type: ignore[attr-defined]
    assert access.path == "/api/v1/_test/ok"  # type: ignore[attr-defined]
    assert access.status == 200  # type: ignore[attr-defined]
    assert isinstance(access.duration_ms, float)  # type: ignore[attr-defined]
    assert "tok-secret" not in str(vars(access))
    assert "tok-header" not in str(vars(access))


def test_request_id_is_attached_to_logs_inside_request(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    client.get("/api/v1/_test/ok", headers={"X-Request-ID": "req-ctx"})

    [access] = [r for r in caplog.records if r.name == "clipforge.access"]
    assert '"request_id": "req-ctx"' in JsonFormatter().format(access)
