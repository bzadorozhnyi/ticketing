from enum import StrEnum

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class UpstreamSettings(BaseModel):
    CATALOG_URL: str = "http://catalog:8000"
    SEAT_HOLD_URL: str = "http://seat-hold:8000"
    BOOKING_URL: str = "http://booking:8000"
    CONNECT_TIMEOUT_SECONDS: float = 2
    TIMEOUT_SECONDS: float = 10


class ServerSettings(BaseModel):
    # Keys of the state a tool sends back with an "input required" answer (the payment
    # question). Every pod must share them, or a retry that reaches another pod fails.
    # The first seals, every one opens: rotate by prepending a new key.
    REQUEST_STATE_KEYS: list[str]
    # A test payment that always succeeds, offered to the user when asked to confirm.
    PAYMENT_TOKEN_HINT: str = "tok_ok"


class LogFormat(StrEnum):
    JSON = "JSON"
    PRETTY = "PRETTY"


class LoggingSettings(BaseModel):
    FORMAT: LogFormat = LogFormat.JSON
    LEVEL: str = "INFO"


class Settings(BaseSettings):
    SERVICE_NAME: str = "mcp"
    UPSTREAMS: UpstreamSettings = UpstreamSettings()
    SERVER: ServerSettings
    LOGGING: LoggingSettings = LoggingSettings()

    # No env_file: this reads only os.environ.
    model_config = SettingsConfigDict(env_nested_delimiter="__")


settings = Settings()
