import shutil
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import psycopg
import pytest

from clipforge.api.v1 import health
from clipforge.main import create_app
from tests.api import api_settings, client_for
from tests.db import libpq_url

READY_URL = "/api/v1/health/ready"
GIB = 1024**3
AI_SECRET_ENV_VARS = ("GEMINI_API_KEY", "OPENROUTER_API_KEY")


class DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


def fake_disk(free: int) -> Callable[[object], DiskUsage]:
    return lambda _path: DiskUsage(total=100 * GIB, used=100 * GIB - free, free=free)


@pytest.fixture(autouse=True)
def plenty_of_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health.shutil, "disk_usage", fake_disk(50 * GIB))


@pytest.fixture(autouse=True)
def ai_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AI_SECRET_ENV_VARS:
        monkeypatch.setenv(name, "test-key")


def ready(database_url: str | None, workspace: Path) -> tuple[int, dict[str, object]]:
    app = create_app(api_settings(database_url, workspace_dir=workspace))
    with client_for(app) as client:
        response = client.get(READY_URL)
    return response.status_code, response.json()


def test_ready_when_all_checks_pass(migrated_database_url: str, tmp_path: Path) -> None:
    status, body = ready(migrated_database_url, tmp_path)

    assert status == 200
    assert body == {
        "status": "ok",
        "failing": [],
        "checks": {
            "database": "ok",
            "workspace": "ok",
            "disk": "ok",
            "ffmpeg": "ok",
            "ai_providers": "ok",
        },
    }


def test_unreachable_database_fails_readiness(tmp_path: Path) -> None:
    status, body = ready("postgresql://nobody@127.0.0.1:1/none", tmp_path)

    assert status == 503
    assert body["status"] == "unavailable"
    assert body["failing"] == ["database", "ai_providers"]


def test_unconfigured_database_fails_readiness(tmp_path: Path) -> None:
    status, body = ready(None, tmp_path)

    assert status == 503
    assert body["failing"] == ["database", "ai_providers"]


def test_missing_workspace_fails_readiness(migrated_database_url: str, tmp_path: Path) -> None:
    status, body = ready(migrated_database_url, tmp_path / "missing")

    assert status == 503
    assert "workspace" in body["failing"]  # type: ignore[operator]


def test_disk_below_reserve_fails_readiness(
    migrated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health.shutil, "disk_usage", fake_disk(5 * GIB - 1))

    status, body = ready(migrated_database_url, tmp_path)

    assert status == 503
    assert body["failing"] == ["disk"]


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe"])
def test_missing_media_binary_fails_readiness(
    migrated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    real_which = shutil.which
    monkeypatch.setattr(
        health.shutil, "which", lambda name: None if name == missing else real_which(name)
    )

    status, body = ready(migrated_database_url, tmp_path)

    assert status == 503
    assert body["failing"] == ["ffmpeg"]


@pytest.mark.parametrize("missing", AI_SECRET_ENV_VARS)
def test_missing_ai_secret_fails_readiness(
    migrated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    missing: str,
) -> None:
    monkeypatch.delenv(missing)

    status, body = ready(migrated_database_url, tmp_path)

    assert status == 503
    assert body["failing"] == ["ai_providers"]
    reported = [getattr(record, "missing_env_vars", None) for record in caplog.records]
    assert [missing] in reported
    assert "test-key" not in caplog.text


def test_unconfigured_selected_model_fails_readiness(
    migrated_database_url: str, tmp_path: Path
) -> None:
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        conn.execute(
            "UPDATE ai_provider_configs SET enabled = false WHERE model = 'gemini-3.5-transcribe'"
        )
        try:
            status, body = ready(migrated_database_url, tmp_path)
        finally:
            conn.execute(
                "UPDATE ai_provider_configs SET enabled = true "
                "WHERE model = 'gemini-3.5-transcribe'"
            )

    assert status == 503
    assert body["failing"] == ["ai_providers"]
