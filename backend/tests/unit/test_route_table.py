"""Checkpoint "API complete": every endpoint in the spec's API table exists and is guarded."""

import re
from pathlib import Path

import pytest

from clipforge.config import Settings
from clipforge.main import API_PREFIX, create_app

pytestmark = pytest.mark.security

SPEC = Path(__file__).resolve().parents[3] / "docs" / "SPEC-001-ClipForge-Bot.md"
_ROW = re.compile(r"^\| (?P<methods>[A-Z/]+) \| `(?P<path>/[^`]*)` \|")
# Telegram sign-in, health probes, and Google's OAuth redirect (checked by its signed state).
PUBLIC = {
    ("POST", "/auth/telegram"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/integrations/drive/callback"),
}


def _shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def spec_endpoints() -> set[tuple[str, str]]:
    endpoints = set()
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        if match := _ROW.match(line):
            for method in match["methods"].split("/"):
                endpoints.add((method, _shape(match["path"])))
    return endpoints


def app_operations() -> dict[tuple[str, str], bool]:
    """(method, path) -> whether it requires the admin bearer token.

    `require_admin` is the only user of the bearer scheme, so OpenAPI's `security` marks it.
    """
    document = create_app(Settings.model_validate({})).openapi()
    return {
        (method.upper(), _shape(path.removeprefix(API_PREFIX))): bool(operation.get("security"))
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
    }


def test_the_spec_table_was_parsed() -> None:
    assert len(spec_endpoints()) >= 30
    assert ("POST", "/clips/{}/affiliate-links/rematch") in spec_endpoints()


def test_every_spec_endpoint_is_implemented() -> None:
    missing = spec_endpoints() - app_operations().keys()

    assert missing == set()


def test_every_non_public_route_requires_an_admin() -> None:
    unguarded = {key for key, guarded in app_operations().items() if not guarded}

    assert unguarded == PUBLIC
