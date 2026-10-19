import psycopg
import pytest

from clipforge.admin_cli import main
from tests.db import libpq_url

TELEGRAM_ID = 5_550_001


def admin_row(url: str) -> tuple[object, ...] | None:
    with psycopg.connect(libpq_url(url)) as conn:
        return conn.execute(
            "SELECT display_name, active FROM admin_users WHERE telegram_user_id = %s",
            (TELEGRAM_ID,),
        ).fetchone()


def test_add_admin_is_idempotent_and_reactivates(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIPFORGE_DATABASE_URL", migrated_database_url)

    assert main(["add-admin", "--telegram-id", str(TELEGRAM_ID), "--name", "Owner"]) == 0
    assert main(["add-admin", "--telegram-id", str(TELEGRAM_ID)]) == 0
    assert admin_row(migrated_database_url) == ("Owner", True)

    assert main(["deactivate-admin", "--telegram-id", str(TELEGRAM_ID)]) == 0
    assert admin_row(migrated_database_url) == ("Owner", False)

    assert main(["add-admin", "--telegram-id", str(TELEGRAM_ID)]) == 0
    assert admin_row(migrated_database_url) == ("Owner", True)


def test_deactivating_unknown_admin_fails(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIPFORGE_DATABASE_URL", migrated_database_url)

    assert main(["deactivate-admin", "--telegram-id", "999999999"]) == 1


@pytest.mark.parametrize("telegram_id", ["0", "-5", "abc"])
def test_invalid_telegram_id_is_rejected(telegram_id: str) -> None:
    with pytest.raises(SystemExit):
        main(["add-admin", "--telegram-id", telegram_id])


def test_missing_database_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIPFORGE_DATABASE_URL", raising=False)

    assert main(["add-admin", "--telegram-id", "1"]) == 2
