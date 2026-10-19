import pytest

from clipforge.config import Settings


def test_settings_read_clipforge_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIPFORGE_LOG_LEVEL", "debug")

    settings = Settings()

    assert settings.log_level == "DEBUG"


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIPFORGE_LOG_LEVEL", raising=False)

    assert Settings().log_level == "INFO"


def test_invalid_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIPFORGE_LOG_LEVEL", "LOUD")

    with pytest.raises(ValueError):
        Settings()


def test_database_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIPFORGE_DATABASE_URL", "postgresql://app@localhost/clipforge")
    monkeypatch.setenv("CLIPFORGE_DB_POOL_SIZE", "4")

    settings = Settings()

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == "postgresql://app@localhost/clipforge"
    assert "app@localhost" not in repr(settings)
    assert settings.db_pool_size == 4
    assert settings.db_disable_prepared_statements is False
