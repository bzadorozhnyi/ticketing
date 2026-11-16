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


class LoggingSettings(BaseModel):
    FORMAT: LogFormat = LogFormat.JSON
    LEVEL: str = "INFO"


class FaultSettings(BaseModel):
    SLOW_SECONDS: float = 3
    DECLINE_RATE: float = 0
    CRASH_BEFORE_COMMIT_RATE: float = 0
    CRASH_AFTER_COMMIT_RATE: float = 0


class RabbitMQSettings(BaseModel):
    HOST: str = "localhost"
    PORT: int = 5672
    USER: str = "ticketing"
    PASS: str = "password"
    QUEUE_PREFIX: str = "payment"
    PREFETCH_COUNT: int = 10
    DELIVERY_LIMIT: int = 10
    HANDLER_TIMEOUT_SECONDS: float = 30
    RETRY_BASE_DELAY_SECONDS: float = 1
    MAX_RETRY_DELAY_SECONDS: float = 30

    @property
    def url(self) -> str:
        return f"amqp://{self.USER}:{self.PASS}@{self.HOST}:{self.PORT}/"


class CoherenceSettings(BaseModel):
    """The check that every charge which took money has its order in booking."""

    # Without it the check does not run: tests and local runs need no booking.
    BOOKING_URL: str | None = None
    INTERVAL_SECONDS: float = 60
    # A charge younger than this is not checked yet.
    GRACE_SECONDS: float = 60
    LOOKBACK_SECONDS: float = 86400
    BATCH_SIZE: int = 1000
    TIMEOUT_SECONDS: float = 5


class Settings(BaseSettings):
    SERVICE_NAME: str = "payment"
    DB: DBSettings
    MIGRATIONS: MigrationSettings = MigrationSettings()
    LOGGING: LoggingSettings = LoggingSettings()
    FAULTS: FaultSettings = FaultSettings()
    RABBITMQ: RabbitMQSettings = RabbitMQSettings()
    COHERENCE: CoherenceSettings = CoherenceSettings()

    # No env_file: this reads only os.environ. Docker compose sets the values
    # directly; tests/conftest.py loads .env.test before anything is imported.
    model_config = SettingsConfigDict(env_nested_delimiter="__")


settings = Settings()
