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


class LoggingSettings(BaseModel):
    FORMAT: LogFormat = LogFormat.JSON
    LEVEL: str = "INFO"


class Settings(BaseSettings):
    SERVICE_NAME: str = "booking"
    DB: DBSettings
    LOGGING: LoggingSettings = LoggingSettings()

    # No env_file: this reads only os.environ. Docker compose sets the values
    # directly; tests/conftest.py loads .env.test before anything is imported.
    model_config = SettingsConfigDict(env_nested_delimiter="__")


settings = Settings()
