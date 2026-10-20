import configparser
import re
from pathlib import Path

import pytest
from pydantic import AliasChoices

from clipforge.config import Settings

DEPLOY_DIR = Path(__file__).resolve().parents[3] / "deploy"
MIGRATIONS_SQL_DIR = Path(__file__).resolve().parents[2] / "migrations" / "sql"
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


def known_env_names() -> set[str]:
    names = set()
    for name, field in Settings.model_fields.items():
        if isinstance(field.validation_alias, AliasChoices):
            names |= {str(choice).upper() for choice in field.validation_alias.choices}
        else:
            names.add(f"CLIPFORGE_{name.upper()}")
    return names


def seeded_ai_secret_refs() -> set[str]:
    """Env var names the seeded `ai_provider_configs` rows read their API keys from."""
    sql = (MIGRATIONS_SQL_DIR / "005_settings.sql").read_text(encoding="utf-8")
    return set(re.findall(r"'([A-Z][A-Z0-9_]*_API_KEY)'", sql))


def test_backend_env_example_only_sets_known_settings() -> None:
    known = known_env_names() | seeded_ai_secret_refs()

    assert set(env_example("backend.env.example")) <= known


def test_backend_env_example_holds_placeholders_only() -> None:
    values = env_example("backend.env.example")

    for field in SECRET_FIELDS:
        assert "CHANGE_ME" in values[f"CLIPFORGE_{field.upper()}"], field
    assert values["YOUTUBE_API_KEY"] == "CHANGE_ME"
    assert seeded_ai_secret_refs() == {"GEMINI_API_KEY", "OPENROUTER_API_KEY"}
    for name in seeded_ai_secret_refs():
        assert values[name] == "CHANGE_ME", name


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


def test_discovery_unit_runs_the_cli_once_as_service_user() -> None:
    service = unit("clipforge-discovery.service")["Service"]

    assert service["Type"] == "oneshot"
    assert service["User"] == "clipforge"
    assert service["EnvironmentFile"] == "/etc/clipforge-bot/backend.env"
    assert (
        service["ExecStart"]
        == "/opt/clipforge-bot/backend/.venv/bin/python -m clipforge.discovery_cli"
    )
    assert service["NoNewPrivileges"] == "true"
    assert service["ProtectSystem"] == "strict"


def test_discovery_timer_fires_daily_at_three_and_catches_up() -> None:
    timer = unit("clipforge-discovery.timer")

    assert timer["Timer"]["OnCalendar"] == "*-*-* 03:00:00"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Timer"]["Unit"] == "clipforge-discovery.service"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_worker_unit_restarts_within_the_resource_limits() -> None:
    service = unit("clipforge-worker.service")["Service"]

    assert service["User"] == "clipforge"
    assert service["EnvironmentFile"] == "/etc/clipforge-bot/backend.env"
    assert (
        service["ExecStart"]
        == "/opt/clipforge-bot/backend/.venv/bin/python -m clipforge.workers.main"
    )
    assert (service["MemoryMax"], service["CPUQuota"]) == ("1800M", "170%")
    assert (service["Restart"], service["RestartSec"]) == ("always", "10")
    assert service["NoNewPrivileges"] == "true"
    assert service["ProtectSystem"] == "strict"
    assert service["PrivateTmp"] == "true"
    assert service["ReadWritePaths"] == "/var/lib/clipforge-bot"


def test_install_script_installs_and_enables_the_worker() -> None:
    script = (DEPLOY_DIR / "install.sh").read_text(encoding="utf-8")

    for name in ("UNITS", "ENABLE_UNITS"):
        line = next(line for line in script.splitlines() if line.startswith(f"{name}="))
        assert "clipforge-worker.service" in line, name
