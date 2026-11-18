import sys

from loguru import logger

from core.settings import LogFormat, settings


def configure_logging() -> None:
    logger.remove()
    logger.configure(extra={"service": settings.SERVICE_NAME})
    logger.add(
        sys.stdout,
        level=settings.LOGGING.LEVEL,
        serialize=settings.LOGGING.FORMAT == LogFormat.JSON,
    )
