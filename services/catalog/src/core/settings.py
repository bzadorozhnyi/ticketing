from enum import StrEnum

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class DBSettings(BaseModel):
    NAME: str
    USER: str
    PASS: str
    HOST: str
    PORT: int = 5432
    CONNECT_TIMEOUT_SECONDS: float = 5
    POOL_PRE_PING: bool = True

    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{self.USER}:{self.PASS}@{self.HOST}:{self.PORT}/{self.NAME}"


class LogFormat(StrEnum):
    JSON = "JSON"
    PRETTY = "PRETTY"


class MigrationSettings(BaseModel):
    LOCK_TIMEOUT_SECONDS: float = 2
    ATTEMPTS: int = 10
    RETRY_BASE_DELAY_SECONDS: float = 1
    MAX_RETRY_DELAY_SECONDS: float = 15


class CacheSettings(BaseModel):
    """Responses of data that does not change, such as seat maps (core/cache/)."""

    TTL_SECONDS: float = 60
    TTL_JITTER: float = 0.1
    MAX_ENTRIES: int = 10_000


class LoggingSettings(BaseModel):
    FORMAT: LogFormat = LogFormat.JSON
    LEVEL: str = "INFO"


class Settings(BaseSettings):
    SERVICE_NAME: str = "catalog"
    DB: DBSettings
    MIGRATIONS: MigrationSettings = MigrationSettings()
    LOGGING: LoggingSettings = LoggingSettings()
    CACHE: CacheSettings = CacheSettings()

    # No env_file: this reads only os.environ. Docker compose sets the values
    # directly; tests/conftest.py loads .env.test before anything is imported.
    model_config = SettingsConfigDict(env_nested_delimiter="__")


settings = Settings()
