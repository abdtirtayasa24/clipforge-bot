from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


class ApiError(Exception):
    """Domain error rendered with the standard envelope; subclasses set status and code."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(body, status_code=status_code, headers=headers)


def internal_error_response() -> JSONResponse:
    return error_response(500, "internal_error", "internal server error")


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    error = cast(ApiError, exc)
    return error_response(error.status_code, error.code, error.message, error.details)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    error = cast(StarletteHTTPException, exc)
    code = _CODES.get(error.status_code, f"http_{error.status_code}")
    message = str(error.detail) if error.detail else HTTPStatus(error.status_code).phrase
    return error_response(error.status_code, code, message, headers=error.headers)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    # Submitted values are omitted: they may hold initData or other secrets.
    details = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in cast(RequestValidationError, exc).errors()
    ]
    return error_response(422, "validation_error", "request validation failed", details)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
