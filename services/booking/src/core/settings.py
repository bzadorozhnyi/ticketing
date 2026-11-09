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


class RabbitMQSettings(BaseModel):
    HOST: str = "localhost"
    PORT: int = 5672
    USER: str = "ticketing"
    PASS: str = "password"
    PREFETCH_COUNT: int = 10

    @property
    def url(self) -> str:
        return f"amqp://{self.USER}:{self.PASS}@{self.HOST}:{self.PORT}/"


class OutboxSettings(BaseModel):
    POLL_INTERVAL_SECONDS: float = 0.5
    BATCH_SIZE: int = 100
    PUBLISH_TIMEOUT_SECONDS: float = 5


class UpstreamSettings(BaseModel):
    SEAT_HOLD_URL: str = "http://seat-hold:8000"
    CATALOG_URL: str = "http://catalog:8000"
    CONNECT_TIMEOUT_SECONDS: float = 1
    TIMEOUT_SECONDS: float = 3


class SagaSettings(BaseModel):
    POLL_INTERVAL_SECONDS: float = 0.5
    RETRY_BASE_DELAY_SECONDS: float = 1
    MAX_RETRY_DELAY_SECONDS: float = 30
    PAYMENT_COMMANDS_QUEUE: str = "payment.commands"
    PAYMENT_REPLIES_QUEUE: str = "payment.replies"
    REPLY_TIMEOUT_SECONDS: float = 30
    REPLY_WAIT_SECONDS: float = 30
    MAX_REPLY_WAIT_SECONDS: float = 120
    GIVE_UP_AFTER_SECONDS: float = 600


class BreakerSettings(BaseModel):
    WINDOW: int = 10
    MIN_CALLS: int = 5
    FAILURE_PERCENT: int = 50
    OPEN_SECONDS: float = 10


class FaultSettings(BaseModel):
    CRASH_RATE: float = 0


class Settings(BaseSettings):
    SERVICE_NAME: str = "booking"
    DB: DBSettings
    LOGGING: LoggingSettings = LoggingSettings()
    RABBITMQ: RabbitMQSettings = RabbitMQSettings()
    OUTBOX: OutboxSettings = OutboxSettings()
    UPSTREAMS: UpstreamSettings = UpstreamSettings()
    SAGA: SagaSettings = SagaSettings()
    BREAKER: BreakerSettings = BreakerSettings()
    FAULTS: FaultSettings = FaultSettings()

    # No env_file: this reads only os.environ. Docker compose sets the values
    # directly; tests/conftest.py loads .env.test before anything is imported.
    model_config = SettingsConfigDict(env_nested_delimiter="__")


settings = Settings()
