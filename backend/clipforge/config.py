from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLIPFORGE_", extra="ignore", env_ignore_empty=True
    )

    log_level: LogLevel = "INFO"
    workspace_dir: Path = Path("/var/lib/clipforge-bot/workspace")
    database_url: SecretStr | None = None
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_disable_prepared_statements: bool = False
    telegram_bot_token: SecretStr | None = None
    session_secret: SecretStr | None = Field(default=None, min_length=32)
    init_data_max_age_seconds: int = Field(default=3600, ge=60, le=86400)
    auth_max_failures: int = Field(default=10, ge=1)
    auth_failure_window_seconds: int = Field(default=300, ge=1)
    # Google Drive OAuth (ADR-015): a "Web application" OAuth client and the Fernet key that
    # encrypts the stored refresh token.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    drive_redirect_uri: str | None = None
    token_encryption_key: SecretStr | None = None
    # Third-party keys keep their standard names, without the CLIPFORGE_ prefix.
    youtube_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("YOUTUBE_API_KEY", "youtube_api_key")
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
