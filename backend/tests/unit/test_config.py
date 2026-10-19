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
