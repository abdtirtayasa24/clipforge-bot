import configparser
import re
from pathlib import Path

import pytest

from clipforge.config import Settings

DEPLOY_DIR = Path(__file__).resolve().parents[3] / "deploy"
SECRET_FIELDS = {"database_url", "telegram_bot_token", "session_secret"}


def env_example(name: str) -> dict[str, str]:
    lines = (DEPLOY_DIR / name).read_text(encoding="utf-8").splitlines()
    pairs = (line.split("=", 1) for line in lines if line and not line.startswith("#"))
    return {key: value for key, value in pairs}


def unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(DEPLOY_DIR / "systemd" / name, encoding="utf-8")
    return parser


def test_backend_env_example_only_sets_known_settings() -> None:
    known = {f"CLIPFORGE_{name.upper()}" for name in Settings.model_fields}

    assert set(env_example("backend.env.example")) <= known


def test_backend_env_example_holds_placeholders_only() -> None:
    values = env_example("backend.env.example")

    for field in SECRET_FIELDS:
        assert "CHANGE_ME" in values[f"CLIPFORGE_{field.upper()}"], field


def test_runtime_env_never_carries_migration_credentials() -> None:
    assert "CLIPFORGE_MIGRATION_DATABASE_URL" not in env_example("backend.env.example")
    assert "CHANGE_ME" in env_example("migration.env.example")["CLIPFORGE_MIGRATION_DATABASE_URL"]


@pytest.mark.parametrize("name", ["backend.env.example", "migration.env.example"])
def test_remote_database_urls_require_verified_tls(name: str) -> None:
    for key, value in env_example(name).items():
        if key.endswith("DATABASE_URL"):
            assert "sslmode=verify-full" in value, key


def test_api_unit_runs_one_local_uvicorn_as_service_user() -> None:
    service = unit("clipforge-api.service")["Service"]

    assert service["User"] == "clipforge"
    assert service["EnvironmentFile"] == "/etc/clipforge-bot/backend.env"
    exec_start = service["ExecStart"]
    assert exec_start.startswith("/opt/clipforge-bot/backend/.venv/bin/uvicorn clipforge.main:app")
    assert re.search(r"--host 127\.0\.0\.1 --port 8000\b", exec_start)
    assert "--workers 1" in exec_start


def test_api_unit_is_hardened() -> None:
    service = unit("clipforge-api.service")["Service"]

    assert service["NoNewPrivileges"] == "true"
    assert service["ProtectSystem"] == "strict"
    assert service["PrivateTmp"] == "true"
    assert service["ReadWritePaths"] == "/var/lib/clipforge-bot"
    assert service["Restart"] == "on-failure"
