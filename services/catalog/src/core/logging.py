import inspect
import logging
import sys

from loguru import logger

from core.settings import LogFormat, settings

PRETTY_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level> | {extra}"
)

# Third-party loggers that write through stdlib logging and would otherwise
# bypass loguru (plain-text lines mixed into the JSON stream).
STDLIB_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy")


class InterceptHandler(logging.Handler):
    """Forwards stdlib logging records to loguru (recipe from the loguru README)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = inspect.currentframe(), 0
        while frame:
            filename = frame.f_code.co_filename
            is_logging = filename == logging.__file__
            is_frozen = "importlib" in filename and "_bootstrap" in filename
            if depth > 0 and not (is_logging or is_frozen):
                break
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def configure_logging() -> None:
    logger.remove()
    logger.configure(extra={"service": settings.SERVICE_NAME})
    if settings.LOGGING.FORMAT == LogFormat.JSON:
        logger.add(sys.stdout, serialize=True, level=settings.LOGGING.LEVEL)
    else:
        logger.add(sys.stdout, format=PRETTY_FORMAT, level=settings.LOGGING.LEVEL)

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in STDLIB_LOGGERS:
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers = [InterceptHandler()]
        stdlib_logger.propagate = False
