from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLIPFORGE_", extra="ignore")

    log_level: LogLevel = "INFO"
    database_url: SecretStr | None = None
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_disable_prepared_statements: bool = False

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
